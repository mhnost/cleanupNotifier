import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config  # noqa: E402


def test_circuit_breaker_threshold_uses_normal_fraction_by_default(monkeypatch):
    monkeypatch.setattr(config, "FIRST_RUN_MODE", False)
    monkeypatch.setattr(config, "CIRCUIT_BREAKER_MAX_FRACTION", 0.5)
    monkeypatch.setattr(config, "CIRCUIT_BREAKER_FIRST_RUN_MAX_FRACTION", 0.8)

    assert config.circuit_breaker_threshold() == 0.5


def test_circuit_breaker_threshold_uses_first_run_fraction_when_opted_in(monkeypatch):
    monkeypatch.setattr(config, "FIRST_RUN_MODE", True)
    monkeypatch.setattr(config, "CIRCUIT_BREAKER_MAX_FRACTION", 0.5)
    monkeypatch.setattr(config, "CIRCUIT_BREAKER_FIRST_RUN_MAX_FRACTION", 0.8)

    assert config.circuit_breaker_threshold() == 0.8
