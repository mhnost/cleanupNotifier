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
                # Raw domain, used to group accounts for the per-domain
                # circuit breaker (see main()) -- kept separate from the
                # display-name version below since a raw domain always
                # maps to exactly one display name, but grouping should
                # stay tied to the source data, not its presentation.
                "raw_domain": candidate.domain,
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
    """Runs the circuit-breaker check per domain (PMO, 2026-08-14) rather
    than once across every account -- domains are inspected one at a
    time, so a bad snapshot for one domain shouldn't block dispatch for
    every other domain. A domain that trips has its rows excluded from
    dispatch this run and is reported via the admin trip email; domains
    that don't trip dispatch normally. If every domain with rows trips
    (so there's nothing left to dispatch), the run still aborts, same
    as before this change.
    """
    rows = classify_all()
    rows_by_domain: dict[str, list[dict]] = {}
    for row in rows:
        rows_by_domain.setdefault(row["raw_domain"], []).append(row)

    counts_by_domain = {
        domain: (
            sum(1 for row in domain_rows if row["outcome"] in ACTIONABLE_OUTCOMES),
            len(domain_rows),
        )
        for domain, domain_rows in rows_by_domain.items()
    }
    results_by_domain = circuit_breaker.check_per_domain(
        counts_by_domain,
        threshold=config.CIRCUIT_BREAKER_MAX_FRACTION,
        min_sample_size=config.CIRCUIT_BREAKER_MIN_SAMPLE_SIZE,
    )
    tripped = {domain: result for domain, result in results_by_domain.items() if result.tripped}

    reportable_rows = [
        row for domain, domain_rows in rows_by_domain.items() if domain not in tripped
        for row in domain_rows
    ]

    if tripped:
        message = circuit_breaker.format_domain_trip_message("email-notification pipeline", tripped)
        email_relay.send_circuit_breaker_trip_email(config.ADMIN_SUMMARY_RECIPIENTS, message)
        if not reportable_rows:
            raise circuit_breaker.CircuitBreakerTripped(message)

    dispatched, missing_email = dispatch(reportable_rows)

    warning_mode = "LIVE" if config.WARNING_DELIVERY_ENABLED else "DRY RUN"
    deactivate_mode = "LIVE" if config.DEACTIVATE_DELIVERY_ENABLED else "DRY RUN"
    mode = f"warnings={warning_mode}, deactivation-notices={deactivate_mode}"
    now = datetime.now(tz=timezone.utc).isoformat()
    print(f"[{now}] Email notification pipeline ({mode})", file=sys.stderr)
    for entry in dispatched:
        print(f"  {entry['action']}: {entry['username']} ({entry['outcome']})", file=sys.stderr)
    if tripped:
        print(
            f"WARNING: circuit breaker tripped for {len(tripped)} domain(s), excluded "
            f"from this run's dispatch: {', '.join(tripped)}",
            file=sys.stderr,
        )
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
