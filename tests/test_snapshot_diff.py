import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from snapshot_diff import SnapshotRecord, find_newly_deactivated  # noqa: E402


def _record(username, disabled, domain_admin=False):
    return SnapshotRecord(username=username, disabled=disabled, domain_admin=domain_admin)


def test_account_disabled_between_snapshots_is_newly_deactivated():
    previous = [_record("alice", disabled=False)]
    current = [_record("alice", disabled=True)]
    assert find_newly_deactivated(previous, current) == ["alice"]


def test_account_disabled_in_both_snapshots_is_not_newly_deactivated():
    previous = [_record("bob", disabled=True)]
    current = [_record("bob", disabled=True)]
    assert find_newly_deactivated(previous, current) == []


def test_account_enabled_in_both_snapshots_is_not_newly_deactivated():
    previous = [_record("carol", disabled=False)]
    current = [_record("carol", disabled=False)]
    assert find_newly_deactivated(previous, current) == []


def test_domain_admin_account_is_excluded_even_if_newly_disabled():
    previous = [_record("admin1", disabled=False, domain_admin=True)]
    current = [_record("admin1", disabled=True, domain_admin=True)]
    assert find_newly_deactivated(previous, current) == []


def test_account_missing_from_previous_snapshot_is_not_newly_deactivated():
    # New account, no prior snapshot to compare against -- not in scope.
    previous = []
    current = [_record("dave", disabled=True)]
    assert find_newly_deactivated(previous, current) == []


def test_account_missing_from_current_snapshot_is_not_newly_deactivated():
    # Deleted-account edge case -- not expected to occur, not treated
    # as a deactivation (see CONTEXT.md).
    previous = [_record("erin", disabled=False)]
    current = []
    assert find_newly_deactivated(previous, current) == []


def test_multiple_accounts_mixed_outcomes():
    previous = [
        _record("alice", disabled=False),
        _record("bob", disabled=True),
        _record("admin1", disabled=False, domain_admin=True),
    ]
    current = [
        _record("alice", disabled=True),
        _record("bob", disabled=True),
        _record("admin1", disabled=True, domain_admin=True),
    ]
    assert find_newly_deactivated(previous, current) == ["alice"]
