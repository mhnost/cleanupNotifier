import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config  # noqa: E402
import user_source  # noqa: E402

NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)

CSV_HEADER = "created,disabled,display_name,dn,domain,domain_admin,email,last_logon,password_last_set,username\n"


def test_uses_last_logon_when_present():
    raw = {
        "username": "alice",
        "email": "alice@example.com",
        "last_logon": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "created": datetime(2020, 1, 1, tzinfo=timezone.utc),
        "domain_admin": False,
    }
    result = user_source.to_candidate_user(raw, now=NOW)
    assert result.username == "alice"
    assert result.email == "alice@example.com"
    assert result.used_created_fallback is False
    assert result.days_since_reference == (NOW - raw["last_logon"]).days


def test_falls_back_to_created_when_never_logged_in():
    # Account created 7 months ago, never logged in -- must be treated as
    # inactive since creation, not exempted for lack of a first logon.
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    raw = {
        "username": "bob",
        "email": "bob@example.com",
        "last_logon": None,
        "created": created,
        "domain_admin": False,
    }
    result = user_source.to_candidate_user(raw, now=NOW)
    assert result.used_created_fallback is True
    assert result.days_since_reference == (NOW - created).days


def test_no_last_logon_and_no_created_is_none():
    raw = {
        "username": "carol",
        "email": "carol@example.com",
        "last_logon": None,
        "created": None,
        "domain_admin": False,
    }
    result = user_source.to_candidate_user(raw, now=NOW)
    assert result.days_since_reference is None
    assert result.used_created_fallback is False


def test_domain_admin_true_is_excluded():
    raw = {
        "username": "admin1",
        "email": "admin1@example.com",
        "last_logon": None,
        "created": datetime(2020, 1, 1, tzinfo=timezone.utc),
        "domain_admin": True,
    }
    result = user_source.to_candidate_user(raw, now=NOW)
    assert result.is_excluded is True


def test_domain_admin_false_is_not_excluded_on_that_basis_alone():
    raw = {
        "username": "regular_user",
        "email": "user@example.com",
        "last_logon": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "created": datetime(2020, 1, 1, tzinfo=timezone.utc),
        "domain_admin": False,
    }
    result = user_source.to_candidate_user(raw, now=NOW)
    assert result.is_excluded is False


def test_missing_domain_admin_field_defaults_to_not_excluded():
    # Missing field should not silently exclude everyone, but also
    # highlights that a record without this field is worth scrutiny.
    raw = {
        "username": "unknown_type",
        "email": None,
        "last_logon": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "created": datetime(2020, 1, 1, tzinfo=timezone.utc),
    }
    result = user_source.to_candidate_user(raw, now=NOW)
    assert result.is_excluded is False


def test_fetch_raw_user_records_reads_csv_and_parses_types(tmp_path, monkeypatch):
    csv_path = tmp_path / "users.csv"
    csv_path.write_text(
        CSV_HEADER
        + "2020-01-01T00:00:00+00:00,false,Alice A,CN=alice,example.com,false,"
        "alice@example.com,2026-01-01T00:00:00+00:00,2026-01-01T00:00:00+00:00,alice\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CURRENT_USER_SOURCE_CSV_PATH", str(csv_path))

    rows = list(user_source.fetch_raw_user_records())

    assert len(rows) == 1
    row = rows[0]
    assert row[config.IDENTIFIER_FIELD] == "alice"
    assert row[config.DISPLAY_NAME_FIELD] == "Alice A"
    assert row[config.EMAIL_FIELD] == "alice@example.com"
    assert row[config.DOMAIN_ADMIN_FIELD] is False
    assert row[config.LAST_LOGON_FIELD] == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert row[config.CREATED_FIELD] == datetime(2020, 1, 1, tzinfo=timezone.utc)


def test_to_candidate_user_carries_display_name_through():
    raw = {
        "username": "alice",
        "display_name": "Alice Andersen",
        "email": "alice@example.com",
        "last_logon": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "created": datetime(2020, 1, 1, tzinfo=timezone.utc),
        "domain_admin": False,
    }
    result = user_source.to_candidate_user(raw, now=NOW)
    assert result.display_name == "Alice Andersen"


def test_fetch_raw_user_records_filters_out_already_disabled_accounts(tmp_path, monkeypatch):
    csv_path = tmp_path / "users.csv"
    csv_path.write_text(
        CSV_HEADER
        + "2020-01-01T00:00:00+00:00,true,Bob B,CN=bob,example.com,false,"
        "bob@example.com,,2020-01-01T00:00:00+00:00,bob\n"
        + "2020-01-01T00:00:00+00:00,false,Carol C,CN=carol,example.com,false,"
        "carol@example.com,,2020-01-01T00:00:00+00:00,carol\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CURRENT_USER_SOURCE_CSV_PATH", str(csv_path))

    rows = list(user_source.fetch_raw_user_records())

    assert [row[config.IDENTIFIER_FIELD] for row in rows] == ["carol"]


def test_fetch_raw_user_records_skips_malformed_row_and_keeps_the_rest(
    tmp_path, monkeypatch, capsys
):
    # An unparseable last_logon value must not crash the whole read --
    # the bad row is skipped and logged, the rest of the dump is kept.
    csv_path = tmp_path / "users.csv"
    csv_path.write_text(
        CSV_HEADER
        + "2020-01-01T00:00:00+00:00,false,Alice A,CN=alice,example.com,false,"
        "alice@example.com,not-a-real-date,2020-01-01T00:00:00+00:00,alice\n"
        + "2020-01-01T00:00:00+00:00,false,Bob B,CN=bob,example.com,false,"
        "bob@example.com,2026-01-01T00:00:00+00:00,2020-01-01T00:00:00+00:00,bob\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CURRENT_USER_SOURCE_CSV_PATH", str(csv_path))

    rows = list(user_source.fetch_raw_user_records())

    assert [row[config.IDENTIFIER_FIELD] for row in rows] == ["bob"]
    err = capsys.readouterr().err
    assert "alice" in err


def test_fetch_raw_user_records_requires_csv_path(monkeypatch):
    monkeypatch.setattr(config, "CURRENT_USER_SOURCE_CSV_PATH", None)
    try:
        list(user_source.fetch_raw_user_records())
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass
