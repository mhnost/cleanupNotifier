import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import circuit_breaker  # noqa: E402
import config  # noqa: E402
import email_notification_report as pipeline  # noqa: E402
import inactivity_logic as logic  # noqa: E402

CSV_HEADER = "created,disabled,display_name,dn,domain,domain_admin,email,last_logon,password_last_set,username\n"


def _row(
    username,
    outcome,
    email="user@example.com",
    reason="test",
    last_logon=None,
    deadline_date=None,
    domain="EG",
    display_name=None,
):
    return {
        "username": username,
        "display_name": display_name,
        "email": email,
        "outcome": outcome,
        "reason": reason,
        "last_logon": last_logon,
        "deadline_date": deadline_date or datetime(2026, 7, 30),
        "domain": domain,
    }


def test_classify_all_reads_csv_and_classifies(tmp_path, monkeypatch):
    csv_path = tmp_path / "current.csv"
    csv_path.write_text(
        CSV_HEADER
        + "2020-01-01T00:00:00+00:00,false,Alice A,CN=alice,example.com,false,"
        "alice@example.com,2020-01-01T00:00:00+00:00,2020-01-01T00:00:00+00:00,alice\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CURRENT_USER_SOURCE_CSV_PATH", str(csv_path))

    rows = pipeline.classify_all()

    assert len(rows) == 1
    assert rows[0]["username"] == "alice"
    assert rows[0]["display_name"] == "Alice A"
    assert rows[0]["email"] == "alice@example.com"
    # Long-inactive since 2020 -- must land on the deactivate outcome.
    assert rows[0]["outcome"] == logic.DEACTIVATE
    assert rows[0]["last_logon"].date() == datetime(2020, 1, 1).date()
    # deadline_date is the reference date (last_logon here) plus the
    # confirmed 187-day deactivation threshold.
    assert (rows[0]["deadline_date"] - rows[0]["last_logon"]).days == config.DEACTIVATE_MIN_DAYS
    # CSV domain is "example.com", which isn't in the display-name
    # mapping, so it should fall back to the raw value unchanged.
    assert rows[0]["domain"] == "example.com"


def test_dispatch_reports_would_send_when_delivery_disabled(monkeypatch):
    monkeypatch.setattr(config, "WARNING_DELIVERY_ENABLED", False)
    monkeypatch.setattr(config, "DEACTIVATE_DELIVERY_ENABLED", False)
    rows = [_row("alice", logic.WARNING), _row("bob", logic.DEACTIVATE)]

    dispatched, missing_email = pipeline.dispatch(rows)

    assert missing_email == []
    assert dispatched == [
        {"username": "alice", "outcome": logic.WARNING, "action": "would_send"},
        {"username": "bob", "outcome": logic.DEACTIVATE, "action": "would_send"},
    ]


def test_dispatch_ignores_non_actionable_outcomes(monkeypatch):
    monkeypatch.setattr(config, "WARNING_DELIVERY_ENABLED", False)
    monkeypatch.setattr(config, "DEACTIVATE_DELIVERY_ENABLED", False)
    rows = [
        _row("carol", logic.OK),
        _row("admin1", logic.EXCLUDED),
        _row("dave", logic.REVIEW),
    ]

    dispatched, missing_email = pipeline.dispatch(rows)

    assert dispatched == []
    assert missing_email == []


def test_dispatch_routes_missing_email_accounts_separately(monkeypatch):
    monkeypatch.setattr(config, "WARNING_DELIVERY_ENABLED", False)
    monkeypatch.setattr(config, "DEACTIVATE_DELIVERY_ENABLED", False)
    rows = [_row("erin", logic.WARNING, email=None)]

    dispatched, missing_email = pipeline.dispatch(rows)

    assert dispatched == []
    assert missing_email == ["erin"]


def _account_row(username, last_logon_iso, domain="example.com"):
    return (
        f"2020-01-01T00:00:00+00:00,false,User {username},CN={username},{domain},false,"
        f"{username}@example.com,{last_logon_iso},2020-01-01T00:00:00+00:00,{username}\n"
    )


def test_main_aborts_without_dispatching_when_circuit_breaker_trips(tmp_path, monkeypatch):
    csv_path = tmp_path / "current.csv"
    rows = "".join(_account_row(f"stale{i}", "2020-01-01T00:00:00+00:00") for i in range(15))
    rows += "".join(_account_row(f"active{i}", "2026-07-25T00:00:00+00:00") for i in range(5))
    csv_path.write_text(CSV_HEADER + rows, encoding="utf-8")
    monkeypatch.setattr(config, "CURRENT_USER_SOURCE_CSV_PATH", str(csv_path))
    monkeypatch.setattr(config, "WARNING_DELIVERY_ENABLED", True)
    monkeypatch.setattr(config, "DEACTIVATE_DELIVERY_ENABLED", True)
    monkeypatch.setattr(config, "ADMIN_SUMMARY_RECIPIENTS", ["onost@eg.no", "nishh@eg.dk"])
    send_calls = []
    monkeypatch.setattr(
        pipeline.email_relay, "send_deactivation_notice_email", lambda *a, **k: send_calls.append(a)
    )
    trip_calls = []
    monkeypatch.setattr(
        pipeline.email_relay,
        "send_circuit_breaker_trip_email",
        lambda recipients, body: trip_calls.append((recipients, body)),
    )

    try:
        pipeline.main()
        assert False, "expected CircuitBreakerTripped"
    except circuit_breaker.CircuitBreakerTripped:
        pass

    # 15/20 (75%) flagged is above threshold -- must abort before dispatch
    # ever calls the relay, live-send config notwithstanding.
    assert send_calls == []
    # The trip must still be routed to the admin recipients.
    assert trip_calls == [(["onost@eg.no", "nishh@eg.dk"], trip_calls[0][1])]
    assert "email-notification pipeline" in trip_calls[0][1]


def test_main_dispatches_the_clean_domain_while_excluding_the_tripped_one(tmp_path, monkeypatch):
    # egrdrift: 15/20 (75%) stale -- trips. kesko: 1/20 (5%) stale -- stays
    # clean. Per-domain policy (PMO, 2026-08-14) means kesko's warning
    # should still be dispatched even though egrdrift tripped.
    rows = "".join(
        _account_row(f"stale{i}", "2020-01-01T00:00:00+00:00", domain="egrdrift") for i in range(15)
    )
    rows += "".join(
        _account_row(f"active{i}", "2026-07-25T00:00:00+00:00", domain="egrdrift") for i in range(5)
    )
    rows += _account_row("stale-kesko", "2020-01-01T00:00:00+00:00", domain="kesko")
    rows += "".join(
        _account_row(f"active-kesko{i}", "2026-07-25T00:00:00+00:00", domain="kesko")
        for i in range(19)
    )
    csv_path = tmp_path / "current.csv"
    csv_path.write_text(CSV_HEADER + rows, encoding="utf-8")
    monkeypatch.setattr(config, "CURRENT_USER_SOURCE_CSV_PATH", str(csv_path))
    monkeypatch.setattr(config, "WARNING_DELIVERY_ENABLED", True)
    monkeypatch.setattr(config, "DEACTIVATE_DELIVERY_ENABLED", True)
    monkeypatch.setattr(config, "ADMIN_SUMMARY_RECIPIENTS", ["onost@eg.no", "nishh@eg.dk"])
    notice_calls = []
    monkeypatch.setattr(
        pipeline.email_relay,
        "send_deactivation_notice_email",
        lambda addr, **k: notice_calls.append(addr),
    )
    trip_calls = []
    monkeypatch.setattr(
        pipeline.email_relay,
        "send_circuit_breaker_trip_email",
        lambda recipients, body: trip_calls.append((recipients, body)),
    )
    monkeypatch.setattr(
        pipeline.email_relay, "send_summary_email", lambda recipients, body: None
    )

    pipeline.main()  # must not raise -- kesko's stale account still gets dispatched

    assert notice_calls == ["stale-kesko@example.com"]
    assert len(trip_calls) == 1
    assert "egrdrift" in trip_calls[0][1]
    assert "kesko" not in trip_calls[0][1]


def test_dispatch_calls_relay_when_delivery_enabled(monkeypatch):
    monkeypatch.setattr(config, "WARNING_DELIVERY_ENABLED", True)
    monkeypatch.setattr(config, "DEACTIVATE_DELIVERY_ENABLED", True)
    warning_calls = []
    notice_calls = []
    monkeypatch.setattr(
        pipeline.email_relay,
        "send_warning_email",
        lambda addr, **kwargs: warning_calls.append(addr),
    )
    monkeypatch.setattr(
        pipeline.email_relay,
        "send_deactivation_notice_email",
        lambda addr, **kwargs: notice_calls.append(addr),
    )
    rows = [
        _row("alice", logic.WARNING, email="alice@example.com"),
        _row("bob", logic.DEACTIVATE, email="bob@example.com"),
    ]

    dispatched, missing_email = pipeline.dispatch(rows)

    assert warning_calls == ["alice@example.com"]
    assert notice_calls == ["bob@example.com"]
    assert dispatched == [
        {"username": "alice", "outcome": logic.WARNING, "action": "sent"},
        {"username": "bob", "outcome": logic.DEACTIVATE, "action": "sent"},
    ]


def test_dispatch_sends_warnings_live_while_deactivate_notices_stay_dry_run(monkeypatch):
    # Warnings go live while deactivation-notices stay would-send-only.
    monkeypatch.setattr(config, "WARNING_DELIVERY_ENABLED", True)
    monkeypatch.setattr(config, "DEACTIVATE_DELIVERY_ENABLED", False)
    warning_calls = []
    notice_calls = []
    monkeypatch.setattr(
        pipeline.email_relay,
        "send_warning_email",
        lambda addr, **kwargs: warning_calls.append(addr),
    )
    monkeypatch.setattr(
        pipeline.email_relay,
        "send_deactivation_notice_email",
        lambda addr, **kwargs: notice_calls.append(addr),
    )
    rows = [
        _row("alice", logic.WARNING, email="alice@example.com"),
        _row("bob", logic.DEACTIVATE, email="bob@example.com"),
    ]

    dispatched, missing_email = pipeline.dispatch(rows)

    assert warning_calls == ["alice@example.com"]
    assert notice_calls == []
    assert dispatched == [
        {"username": "alice", "outcome": logic.WARNING, "action": "sent"},
        {"username": "bob", "outcome": logic.DEACTIVATE, "action": "would_send"},
    ]


def test_dispatch_passes_display_name_through_to_relay(monkeypatch):
    monkeypatch.setattr(config, "WARNING_DELIVERY_ENABLED", True)
    monkeypatch.setattr(config, "DEACTIVATE_DELIVERY_ENABLED", True)
    warning_kwargs = []
    monkeypatch.setattr(
        pipeline.email_relay,
        "send_warning_email",
        lambda addr, **kwargs: warning_kwargs.append(kwargs),
    )
    rows = [_row("alice", logic.WARNING, email="alice@example.com", display_name="Alice Andersen")]

    pipeline.dispatch(rows)

    assert warning_kwargs[0]["display_name"] == "Alice Andersen"


def test_main_sends_admin_summary_with_counts(tmp_path, monkeypatch):
    csv_path = tmp_path / "current.csv"
    rows = _account_row("stale0", "2020-01-01T00:00:00+00:00")
    rows += _account_row("active0", "2026-07-25T00:00:00+00:00")
    csv_path.write_text(CSV_HEADER, encoding="utf-8")
    csv_path.write_text(CSV_HEADER + rows, encoding="utf-8")
    monkeypatch.setattr(config, "CURRENT_USER_SOURCE_CSV_PATH", str(csv_path))
    monkeypatch.setattr(config, "WARNING_DELIVERY_ENABLED", False)
    monkeypatch.setattr(config, "DEACTIVATE_DELIVERY_ENABLED", False)
    monkeypatch.setattr(config, "ADMIN_SUMMARY_RECIPIENTS", ["onost@eg.no", "nishh@eg.dk"])
    summary_calls = []
    monkeypatch.setattr(
        pipeline.email_relay,
        "send_summary_email",
        lambda recipients, body: summary_calls.append((recipients, body)),
    )

    pipeline.main()

    assert len(summary_calls) == 1
    recipients, body = summary_calls[0]
    assert recipients == ["onost@eg.no", "nishh@eg.dk"]
    assert "Deactivation-notice sent: 1" in body
