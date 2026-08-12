"""
Pure diff logic for the deactivation-tracking pipeline (see CONTEXT.md
"New Design: Snapshot Diff").

Deliberately has no file/backend dependency: this is the piece that
decides which accounts count as "newly deactivated," so it's the most
heavily tested and most carefully reasoned about, independent of how
the CSV snapshots are read.
"""

from dataclasses import dataclass
from typing import Iterable, List


@dataclass(frozen=True)
class SnapshotRecord:
    username: str
    disabled: bool
    domain_admin: bool


def find_newly_deactivated(
    previous: Iterable[SnapshotRecord], current: Iterable[SnapshotRecord]
) -> List[str]:
    """Return the usernames of accounts that were enabled in `previous`
    and are disabled in `current`.

    Matched by username, confirmed stable/unique across snapshots (PMO,
    2026-07-23). domain_admin accounts are excluded -- service accounts
    are always also flagged domain_admin, and both service and admin
    accounts are always untouched and never actually deactivated, so
    they're out of scope for this count entirely.

    Accounts present in `previous` but missing entirely from `current`
    (removed from AD outright, not just disabled) are not treated as
    deactivated -- this isn't expected to occur per current guidance;
    revisit if it does.
    """
    previous_by_username = {record.username: record for record in previous}
    newly_deactivated = []
    for record in current:
        if record.domain_admin:
            continue
        prev = previous_by_username.get(record.username)
        if prev is None:
            continue
        if not prev.disabled and record.disabled:
            newly_deactivated.append(record.username)
    return newly_deactivated
