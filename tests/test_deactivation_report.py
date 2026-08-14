import csv
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import circuit_breaker  # noqa: E402
import config  # noqa: E402
import deactivation_report  # noqa: E402

CSV_HEADER = "created,disabled,display_name,dn,domain,domain_admin,email,last_logon,password_last_set,username\n"


def test_require_snapshot_paths_raises_when_missing(monkeypatch):
    monkeypatch.setattr(config, "PREVIOUS_USER_SOURCE_CSV_PATH", None)
    monkeypatch.setattr(config, "CURRENT_USER_SOURCE_CSV_PATH", "somewhere.csv")
    try:
        config.require_snapshot_paths()
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_require_snapshot_paths_passes_when_both_set(monkeypatch):
    monkeypatch.setattr(config, "PREVIOUS_USER_SOURCE_CSV_PATH", "a.csv")
    monkeypatch.setattr(config, "CURRENT_USER_SOURCE_CSV_PATH", "b.csv")
    config.require_snapshot_paths()


def test_run_finds_newly_deactivated_accounts(tmp_path, monkeypatch):
    previous_path = tmp_path / "previous.csv"
    current_path = tmp_path / "current.csv"
    previous_path.write_text(
        CSV_HEADER
        + "2020-01-01T00:00:00+00:00,false,Alice A,CN=alice,example.com,false,"
        "alice@example.com,2026-01-01T00:00:00+00:00,2026-01-01T00:00:00+00:00,alice\n",
        encoding="utf-8",
    )
    current_path.write_text(
        CSV_HEADER
        + "2020-01-01T00:00:00+00:00,true,Alice A,CN=alice,example.com,false,"
        "alice@example.com,2026-01-01T00:00:00+00:00,2026-01-01T00:00:00+00:00,alice\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "PREVIOUS_USER_SOURCE_CSV_PATH", str(previous_path))
    monkeypatch.setattr(config, "CURRENT_USER_SOURCE_CSV_PATH", str(current_path))

    assert deactivation_report.run() == ["alice"]


def _user_row(username, disabled, domain="example.com"):
    return (
        f"2020-01-01T00:00:00+00:00,{disabled},User {username},CN={username},"
        f"{domain},false,{username}@example.com,2026-01-01T00:00:00+00:00,"
        f"2026-01-01T00:00:00+00:00,{username}\n"
    )


def test_main_aborts_without_writing_report_when_circuit_breaker_trips(tmp_path, monkeypatch):
    usernames = [f"user{i}" for i in range(20)]
    previous_path = tmp_path / "previous.csv"
    current_path = tmp_path / "current.csv"
    previous_path.write_text(
        CSV_HEADER + "".join(_user_row(u, "false") for u in usernames), encoding="utf-8"
    )
    # 15/20 (75%) newly disabled -- well above the default 50% threshold.
    current_path.write_text(
        CSV_HEADER
        + "".join(_user_row(u, "true") for u in usernames[:15])
        + "".join(_user_row(u, "false") for u in usernames[15:]),
        encoding="utf-8",
    )
    report_path = tmp_path / "report.csv"
    monkeypatch.setattr(config, "PREVIOUS_USER_SOURCE_CSV_PATH", str(previous_path))
    monkeypatch.setattr(config, "CURRENT_USER_SOURCE_CSV_PATH", str(current_path))
    monkeypatch.setattr(config, "NEW_DEACTIVATIONS_REPORT_CSV_PATH", str(report_path))
    monkeypatch.setattr(config, "ADMIN_SUMMARY_RECIPIENTS", ["onost@eg.no", "nishh@eg.dk"])
    trip_calls = []
    monkeypatch.setattr(
        deactivation_report.email_relay,
        "send_circuit_breaker_trip_email",
        lambda recipients, body: trip_calls.append((recipients, body)),
    )

    try:
        deactivation_report.main()
        assert False, "expected CircuitBreakerTripped"
    except circuit_breaker.CircuitBreakerTripped:
        pass

    assert not report_path.exists()
    # The trip must still be routed to the admin recipients.
    assert trip_calls == [
        (["onost@eg.no", "nishh@eg.dk"], trip_calls[0][1]),
    ]
    assert "deactivation-diff pipeline" in trip_calls[0][1]


def test_main_writes_report_when_circuit_breaker_not_tripped(tmp_path, monkeypatch):
    previous_path = tmp_path / "previous.csv"
    current_path = tmp_path / "current.csv"
    previous_path.write_text(CSV_HEADER + _user_row("alice", "false"), encoding="utf-8")
    current_path.write_text(CSV_HEADER + _user_row("alice", "true"), encoding="utf-8")
    report_path = tmp_path / "report.csv"
    monkeypatch.setattr(config, "PREVIOUS_USER_SOURCE_CSV_PATH", str(previous_path))
    monkeypatch.setattr(config, "CURRENT_USER_SOURCE_CSV_PATH", str(current_path))
    monkeypatch.setattr(config, "NEW_DEACTIVATIONS_REPORT_CSV_PATH", str(report_path))

    # 1/1 flagged is 100%, but below the default min sample size (20),
    # so it must not trip.
    deactivation_report.main()

    assert report_path.exists()


def test_main_excludes_only_the_tripped_domain_when_another_domain_is_clean(tmp_path, monkeypatch):
    # egrdrift: 15/20 (75%) newly disabled -- trips. kesko: 1/20 (5%) --
    # stays clean. Per-domain policy (PMO, 2026-08-14) means kesko's
    # account should still make it into the report even though egrdrift
    # tripped.
    bad_domain_users = [f"bad{i}" for i in range(20)]
    good_domain_users = [f"good{i}" for i in range(20)]
    previous_path = tmp_path / "previous.csv"
    current_path = tmp_path / "current.csv"
    previous_path.write_text(
        CSV_HEADER
        + "".join(_user_row(u, "false", domain="egrdrift") for u in bad_domain_users)
        + "".join(_user_row(u, "false", domain="kesko") for u in good_domain_users),
        encoding="utf-8",
    )
    current_path.write_text(
        CSV_HEADER
        + "".join(_user_row(u, "true", domain="egrdrift") for u in bad_domain_users[:15])
        + "".join(_user_row(u, "false", domain="egrdrift") for u in bad_domain_users[15:])
        + "".join(_user_row(u, "true", domain="kesko") for u in good_domain_users[:1])
        + "".join(_user_row(u, "false", domain="kesko") for u in good_domain_users[1:]),
        encoding="utf-8",
    )
    report_path = tmp_path / "report.csv"
    monkeypatch.setattr(config, "PREVIOUS_USER_SOURCE_CSV_PATH", str(previous_path))
    monkeypatch.setattr(config, "CURRENT_USER_SOURCE_CSV_PATH", str(current_path))
    monkeypatch.setattr(config, "NEW_DEACTIVATIONS_REPORT_CSV_PATH", str(report_path))
    monkeypatch.setattr(config, "ADMIN_SUMMARY_RECIPIENTS", ["onost@eg.no", "nishh@eg.dk"])
    trip_calls = []
    monkeypatch.setattr(
        deactivation_report.email_relay,
        "send_circuit_breaker_trip_email",
        lambda recipients, body: trip_calls.append((recipients, body)),
    )

    deactivation_report.main()  # must not raise -- kesko's data still gets written

    with open(report_path, newline="", encoding="utf-8") as f:
        reported_usernames = {row["username"] for row in csv.DictReader(f)}
    assert reported_usernames == {"good0"}
    assert len(trip_calls) == 1
    assert "egrdrift" in trip_calls[0][1]
    assert "kesko" not in trip_calls[0][1]


def test_write_report_writes_username_and_timestamp_only(tmp_path):
    report_path = tmp_path / "report.csv"
    deactivation_report.write_report(["alice", "bob"], str(report_path))

    with open(report_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert [row["username"] for row in rows] == ["alice", "bob"]
    assert set(rows[0].keys()) == {"username", "timestamp"}
    assert rows[0]["timestamp"]
