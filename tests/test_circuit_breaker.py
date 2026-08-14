import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import circuit_breaker  # noqa: E402


def test_check_does_not_trip_below_threshold():
    result = circuit_breaker.check(flagged_count=3, total_count=100, threshold=0.5)
    assert result.tripped is False
    assert result.fraction == 0.03


def test_check_trips_above_threshold():
    result = circuit_breaker.check(flagged_count=60, total_count=100, threshold=0.5)
    assert result.tripped is True
    assert result.fraction == 0.6


def test_check_does_not_trip_exactly_at_threshold():
    # Threshold is exceeded, not merely met.
    result = circuit_breaker.check(flagged_count=50, total_count=100, threshold=0.5)
    assert result.tripped is False


def test_check_never_trips_below_min_sample_size():
    # 1/1 is 100%, but a single-account run isn't a meaningful sample.
    result = circuit_breaker.check(
        flagged_count=1, total_count=1, threshold=0.5, min_sample_size=20
    )
    assert result.tripped is False


def test_check_trips_once_min_sample_size_is_met():
    result = circuit_breaker.check(
        flagged_count=15, total_count=20, threshold=0.5, min_sample_size=20
    )
    assert result.tripped is True


def test_check_never_trips_on_empty_total():
    result = circuit_breaker.check(flagged_count=0, total_count=0, threshold=0.5)
    assert result.tripped is False
    assert result.fraction == 0.0


def test_raise_if_tripped_raises_with_label_and_stats():
    try:
        circuit_breaker.raise_if_tripped(
            flagged_count=80, total_count=100, threshold=0.5, label="test pipeline"
        )
        assert False, "expected CircuitBreakerTripped"
    except circuit_breaker.CircuitBreakerTripped as exc:
        message = str(exc)
        assert "test pipeline" in message
        assert "80/100" in message


def test_raise_if_tripped_returns_result_when_not_tripped():
    result = circuit_breaker.raise_if_tripped(
        flagged_count=1, total_count=100, threshold=0.5, label="test pipeline"
    )
    assert result.tripped is False


def test_check_per_domain_evaluates_each_domain_independently():
    results = circuit_breaker.check_per_domain(
        {
            "egrdrift": (80, 100),  # 80% -- trips
            "egrtest": (10, 100),  # 10% -- does not trip
        },
        threshold=0.5,
    )
    assert results["egrdrift"].tripped is True
    assert results["egrtest"].tripped is False


def test_check_per_domain_respects_min_sample_size_per_domain():
    results = circuit_breaker.check_per_domain(
        {"small-domain": (1, 1)},
        threshold=0.5,
        min_sample_size=20,
    )
    assert results["small-domain"].tripped is False


def test_format_domain_trip_message_includes_every_tripped_domain_and_threshold():
    tripped = circuit_breaker.check_per_domain(
        {"egrdrift": (80, 100), "kesko": (60, 100)}, threshold=0.5
    )
    message = circuit_breaker.format_domain_trip_message("test pipeline", tripped)
    assert "test pipeline" in message
    assert "egrdrift: 80/100 (80.0%)" in message
    assert "kesko: 60/100 (60.0%)" in message
    assert "50%" in message
