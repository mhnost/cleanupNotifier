"""
Entry point wiring per-user classification (inactivity_logic.py,
user_source.py) to the SMTP relay (email_relay.py) -- see CONTEXT.md.

config.WARNING_DELIVERY_ENABLED and config.DEACTIVATE_DELIVERY_ENABLED
gate the two outcomes independently (PMO/IT, 2026-08-12), so warning
emails can go live independently of deactivation-notice emails per the
confirmed staged rollout. Both default False: classify_all()
classifies every account regardless, and dispatch() reports what it
*would* send for whichever outcome's flag is still off, without ever
calling email_relay for that outcome. Flipping either flag to True is
a separate, explicit decision from wiring the pieces together.

Rate limits/bounce handling are deliberately out of scope (confirmed
by PO, 2026-08-13). The missing-email case IS handled below: accounts
with WARNING/DEACTIVATE outcomes but no email on file have their
notification skipped (PMO, 2026-08-07) rather than guessing an
address -- but still reported (not silently dropped) so it's visible
in the run summary. main() also sends a per-run admin summary email
(counts only, no PII) to config.ADMIN_SUMMARY_RECIPIENTS regardless of
either delivery flag, since it's useful during the dry-run phase too.
"""

import sys
from datetime import datetime, timedelta, timezone

import circuit_breaker
import config
import email_relay
import inactivity_logic as logic
import user_source

ACTIONABLE_OUTCOMES = {logic.WARNING, logic.DEACTIVATE}


def classify_all(now=None) -> list[dict]:
    """Read the current snapshot and classify every account.

    Each row also carries last_logon and deadline_date -- the email
    body needs both (see email_relay.py), not just the outcome/reason
    used for the summary print.
    """
    rows = []
    for raw in user_source.fetch_raw_user_records():
        candidate = user_source.to_candidate_user(raw, now=now)
        result = logic.classify_account(
            days_since_reference=candidate.days_since_reference,
            is_excluded=candidate.is_excluded,
        )
        deadline_date = None
        if candidate.reference_date is not None:
            deadline_date = candidate.reference_date + timedelta(days=config.DEACTIVATE_MIN_DAYS)
        rows.append(
            {
                "username": candidate.username,
                "display_name": candidate.display_name,
                "email": candidate.email,
                "outcome": result.outcome,
                "reason": result.reason,
                "last_logon": candidate.last_logon,
                "deadline_date": deadline_date,
                "domain": email_relay.resolve_domain_display_name(candidate.domain),
            }
        )
    return rows


_DELIVERY_FLAG_BY_OUTCOME = {
    logic.WARNING: lambda: config.WARNING_DELIVERY_ENABLED,
    logic.DEACTIVATE: lambda: config.DEACTIVATE_DELIVERY_ENABLED,
}


def dispatch(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """Route WARNING/DEACTIVATE outcomes to the relay.

    Each outcome is gated by its own flag (config.WARNING_DELIVERY_ENABLED
    / config.DEACTIVATE_DELIVERY_ENABLED) -- a run can
    have warnings live while deactivation-notices are still dry-run, or
    vice versa. Returns (dispatched, missing_email) where `dispatched`
    entries have an "action" of "sent" (that outcome's flag is True) or
    "would_send" (it's False -- nothing is sent for that outcome), and
    `missing_email` lists usernames whose notification was skipped
    because they have no email address on file (PMO, 2026-08-07:
    skip the notification rather than guessing an address) -- still
    reported here rather than silently dropped, so it's visible in the
    run summary.
    """
    dispatched = []
    missing_email = []
    for row in rows:
        if row["outcome"] not in ACTIONABLE_OUTCOMES:
            continue
        if not row["email"]:
            missing_email.append(row["username"])
            continue

        if not _DELIVERY_FLAG_BY_OUTCOME[row["outcome"]]():
            dispatched.append(
                {"username": row["username"], "outcome": row["outcome"], "action": "would_send"}
            )
            continue

        if row["outcome"] == logic.WARNING:
            email_relay.send_warning_email(
                row["email"],
                last_logon=row["last_logon"],
                deadline_date=row["deadline_date"],
                domain=row["domain"],
                display_name=row.get("display_name"),
            )
        else:
            email_relay.send_deactivation_notice_email(
                row["email"],
                last_logon=row["last_logon"],
                deadline_date=row["deadline_date"],
                domain=row["domain"],
                display_name=row.get("display_name"),
            )
        dispatched.append(
            {"username": row["username"], "outcome": row["outcome"], "action": "sent"}
        )
    return dispatched, missing_email


def main() -> None:
    rows = classify_all()
    actionable_count = sum(1 for row in rows if row["outcome"] in ACTIONABLE_OUTCOMES)
    try:
        circuit_breaker.raise_if_tripped(
            flagged_count=actionable_count,
            total_count=len(rows),
            threshold=config.CIRCUIT_BREAKER_MAX_FRACTION,
            min_sample_size=config.CIRCUIT_BREAKER_MIN_SAMPLE_SIZE,
            label="email-notification pipeline",
        )
    except circuit_breaker.CircuitBreakerTripped as exc:
        email_relay.send_circuit_breaker_trip_email(config.ADMIN_SUMMARY_RECIPIENTS, str(exc))
        raise
    dispatched, missing_email = dispatch(rows)

    warning_mode = "LIVE" if config.WARNING_DELIVERY_ENABLED else "DRY RUN"
    deactivate_mode = "LIVE" if config.DEACTIVATE_DELIVERY_ENABLED else "DRY RUN"
    mode = f"warnings={warning_mode}, deactivation-notices={deactivate_mode}"
    now = datetime.now(tz=timezone.utc).isoformat()
    print(f"[{now}] Email notification pipeline ({mode})", file=sys.stderr)
    for entry in dispatched:
        print(f"  {entry['action']}: {entry['username']} ({entry['outcome']})", file=sys.stderr)
    if missing_email:
        print(
            f"WARNING: {len(missing_email)} account(s) needed a warning/deactivation-"
            f"notice email but had no address on file -- notification skipped "
            f"(PMO, 2026-08-07): {', '.join(missing_email)}",
            file=sys.stderr,
        )

    warned_count = sum(1 for e in dispatched if e["outcome"] == logic.WARNING)
    notified_count = sum(1 for e in dispatched if e["outcome"] == logic.DEACTIVATE)
    summary_body = email_relay.build_summary_body(
        mode=mode,
        warned_count=warned_count,
        notified_count=notified_count,
        missing_email_count=len(missing_email),
    )
    email_relay.send_summary_email(config.ADMIN_SUMMARY_RECIPIENTS, summary_body)


if __name__ == "__main__":
    main()
