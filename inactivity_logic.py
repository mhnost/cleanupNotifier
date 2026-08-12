"""
Pure decision logic for per-user email classification (rebuilt
2026-07-23 to drive the revived warning/deactivation-notification
emails -- see CONTEXT.md and email_relay.py).

Deliberately has no backend/network/email dependency and no side effects,
so it can be unit tested exhaustively and reasoned about independently of
the plumbing that fetches data or sends mail. This is the piece that
actually decides "does this account get a warning or a deactivation-
notification email" -- it should be the most carefully reviewed and
tested part of the whole email pipeline.

Note: this only decides which *email* to send. Neither outcome here
disables an account -- that's done by a separate system (deactivation
tracking) or not at all (warning). See config.py for the thresholds.
"""

from dataclasses import dataclass
from typing import Optional

import config

# Possible outcomes. Deliberately strings, not raw booleans, so report
# output and logs are self-describing.
OK = "ok"
WARNING = "warning"
DEACTIVATE = "deactivate"
EXCLUDED = "excluded"
REVIEW = "review"  # ambiguous cases that should never be auto-actioned


@dataclass(frozen=True)
class Classification:
    outcome: str
    reason: str


def classify_account(
    days_since_reference: Optional[int],
    is_excluded: bool,
) -> Classification:
    """
    Decide which email (if any) an account should receive, based purely on:
      - days_since_reference: whole days since the account's activity
        reference point -- last_logon if it has ever logged in, or
        created if it never has (see user_source.to_candidate_user for
        that fallback logic). None only if neither value was available
        at all, which signals a genuine data problem.
      - is_excluded: True if this account matched an exclusion rule
        (domain_admin -- confirmed sufficient for both admin and service
        accounts, see user_source.to_candidate_user and
        config.SERVICE_ACCOUNT_MARKING_CONFIRMED) and must never be
        warned or notified of deactivation.

    Returns a Classification with an outcome and a human-readable reason.
    WARNING means "send the warning email"; DEACTIVATE means "send the
    deactivation-notification email" -- neither disables the account.
    """
    if is_excluded:
        return Classification(EXCLUDED, "matches admin/service exclusion rule")

    if days_since_reference is None:
        # No last_logon AND no created date -- a genuine data gap. Don't
        # guess -- surface it for a human to look at instead of auto-classifying.
        return Classification(REVIEW, "no last_logon or created date recorded")

    if days_since_reference < 0:
        # Clock skew or bad data -- never act on this, flag it instead.
        return Classification(REVIEW, "negative days-since-reference (bad data?)")

    if days_since_reference < config.WARNING_MIN_DAYS:
        return Classification(OK, f"active {days_since_reference} days ago")

    if days_since_reference <= config.WARNING_MAX_DAYS:
        return Classification(
            WARNING,
            f"last activity {days_since_reference} days ago, in warning window "
            f"({config.WARNING_MIN_DAYS}-{config.WARNING_MAX_DAYS} days)",
        )

    if days_since_reference >= config.DEACTIVATE_MIN_DAYS:
        return Classification(
            DEACTIVATE,
            f"last activity {days_since_reference} days ago, "
            f">= {config.DEACTIVATE_MIN_DAYS}-day deactivation-notice threshold",
        )

    # Unreachable if WARNING_MAX_DAYS + 1 == DEACTIVATE_MIN_DAYS, but kept
    # explicit in case thresholds are changed to leave a gap.
    return Classification(
        REVIEW,
        f"last activity {days_since_reference} days ago falls between the "
        "warning and deactivation-notice windows -- check threshold configuration",
    )
