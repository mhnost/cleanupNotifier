"""
Circuit-breaker check shared by the diff pipeline
(deactivation_report.py) and the email pipeline
(email_notification_report.py).

Guards against an implausibly large fraction of accounts being flagged
in a single run (e.g. a bad CSV dump misclassifying most accounts as
inactive) by aborting the run loudly instead of silently writing a
mass report or sending a wave of emails.

Deliberately pure/no I/O, same as snapshot_diff.py and
inactivity_logic.py -- the call sites decide what "flagged" and
"total" mean for their pipeline and what to do once tripped.
"""

from dataclasses import dataclass


class CircuitBreakerTripped(Exception):
    """Raised by raise_if_tripped() when the flagged fraction exceeds
    the configured threshold on a large-enough sample."""


@dataclass(frozen=True)
class CircuitBreakerResult:
    flagged_count: int
    total_count: int
    fraction: float
    threshold: float
    tripped: bool


def check(
    flagged_count: int, total_count: int, threshold: float, min_sample_size: int = 0
) -> CircuitBreakerResult:
    """Pure check: does flagged_count/total_count exceed threshold?

    Never trips when total_count is below min_sample_size -- a fraction
    computed from a handful of accounts (e.g. a test/staging run) isn't
    a meaningful signal either way. Never trips on total_count == 0
    either (nothing to compare against, e.g. an empty snapshot).
    """
    fraction = flagged_count / total_count if total_count else 0.0
    tripped = total_count >= max(min_sample_size, 1) and fraction > threshold
    return CircuitBreakerResult(
        flagged_count=flagged_count,
        total_count=total_count,
        fraction=fraction,
        threshold=threshold,
        tripped=tripped,
    )


def raise_if_tripped(
    flagged_count: int,
    total_count: int,
    threshold: float,
    label: str,
    min_sample_size: int = 0,
) -> CircuitBreakerResult:
    """Same as check(), but raises CircuitBreakerTripped if tripped.

    Call sites want to abort the run loudly rather than silently write
    a suspicious report or send a wave of emails, so this is the
    entry point main() should call.
    """
    result = check(flagged_count, total_count, threshold, min_sample_size=min_sample_size)
    if result.tripped:
        raise CircuitBreakerTripped(
            f"{label}: {result.flagged_count}/{result.total_count} accounts "
            f"({result.fraction:.1%}) exceeded the {result.threshold:.0%} "
            "circuit-breaker threshold -- aborting without writing output. "
            "This likely indicates a bad snapshot/CSV rather than a real "
            "mass deactivation event."
        )
    return result


def check_per_domain(
    flagged_and_total_by_domain: dict,
    threshold: float,
    min_sample_size: int = 0,
) -> dict:
    """Same as check(), but scoped to each domain independently.

    Domains are inspected one at a time (PMO, 2026-08-14), so one
    domain's bad snapshot shouldn't abort every other domain's run the
    way a single org-wide fraction check would. Keys are domain names;
    values are (flagged_count, total_count) pairs in, CircuitBreakerResult
    out.
    """
    return {
        domain: check(flagged, total, threshold, min_sample_size=min_sample_size)
        for domain, (flagged, total) in flagged_and_total_by_domain.items()
    }


def format_domain_trip_message(label: str, tripped_by_domain: dict) -> str:
    """Build a single trip-notification message summarizing every domain
    that tripped in a per-domain check (see check_per_domain()).

    tripped_by_domain maps domain name -> CircuitBreakerResult, and is
    expected to already be filtered down to the tripped ones.
    """
    details = "; ".join(
        f"{domain}: {result.flagged_count}/{result.total_count} ({result.fraction:.1%})"
        for domain, result in tripped_by_domain.items()
    )
    # All domains from a single check_per_domain() call share the same
    # configured threshold, so any one result's .threshold describes it.
    threshold = next(iter(tripped_by_domain.values())).threshold
    return (
        f"{label}: circuit breaker tripped for domain(s) {details} -- each "
        f"exceeded its {threshold:.0%} circuit-breaker threshold. Accounts "
        "in those domain(s) are excluded from this run; other domains "
        "proceeded normally."
    )
