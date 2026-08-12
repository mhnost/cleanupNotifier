import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config  # noqa: E402
import inactivity_logic as logic  # noqa: E402


def test_excluded_account_is_never_classified_by_days(monkeypatch):
    result = logic.classify_account(days_since_reference=1000, is_excluded=True)
    assert result.outcome == logic.EXCLUDED


def test_active_account_is_ok():
    result = logic.classify_account(days_since_reference=config.WARNING_MIN_DAYS - 1, is_excluded=False)
    assert result.outcome == logic.OK


def test_account_at_warning_min_days_is_warning():
    result = logic.classify_account(days_since_reference=config.WARNING_MIN_DAYS, is_excluded=False)
    assert result.outcome == logic.WARNING


def test_account_at_warning_max_days_is_warning():
    result = logic.classify_account(days_since_reference=config.WARNING_MAX_DAYS, is_excluded=False)
    assert result.outcome == logic.WARNING


def test_account_at_deactivate_min_days_is_deactivate():
    result = logic.classify_account(days_since_reference=config.DEACTIVATE_MIN_DAYS, is_excluded=False)
    assert result.outcome == logic.DEACTIVATE


def test_account_well_past_deactivate_threshold_is_deactivate():
    result = logic.classify_account(days_since_reference=config.DEACTIVATE_MIN_DAYS + 100, is_excluded=False)
    assert result.outcome == logic.DEACTIVATE


def test_missing_days_since_reference_is_review():
    result = logic.classify_account(days_since_reference=None, is_excluded=False)
    assert result.outcome == logic.REVIEW


def test_negative_days_since_reference_is_review():
    result = logic.classify_account(days_since_reference=-5, is_excluded=False)
    assert result.outcome == logic.REVIEW


def test_gap_between_warning_and_deactivate_windows_is_review(monkeypatch):
    # Artificially widen the gap to exercise the "unreachable in practice"
    # branch that guards against a misconfigured threshold gap.
    monkeypatch.setattr(config, "WARNING_MAX_DAYS", 186)
    monkeypatch.setattr(config, "DEACTIVATE_MIN_DAYS", 190)
    result = logic.classify_account(days_since_reference=188, is_excluded=False)
    assert result.outcome == logic.REVIEW
