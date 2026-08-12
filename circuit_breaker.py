"""
Circuit-breaker check shared by the diff pipeline
(deactivation_report.py) and the email pipeline
(email_notification_report.py) -- see TODO.md item 8.

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
