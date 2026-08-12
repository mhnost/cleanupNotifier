import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import snapshot_source  # noqa: E402

CSV_HEADER = "created,disabled,display_name,dn,domain,domain_admin,email,last_logon,password_last_set,username\n"


def test_read_snapshot_parses_disabled_and_domain_admin(tmp_path):
    csv_path = tmp_path / "snapshot.csv"
    csv_path.write_text(
        CSV_HEADER
        + "2020-01-01T00:00:00+00:00,true,Alice A,CN=alice,example.com,false,"
        "alice@example.com,2026-01-01T00:00:00+00:00,2026-01-01T00:00:00+00:00,alice\n"
        + "2020-01-01T00:00:00+00:00,false,Admin A,CN=admin1,example.com,true,"
        "admin1@example.com,,2020-01-01T00:00:00+00:00,admin1\n",
        encoding="utf-8",
    )

    rows = list(snapshot_source.read_snapshot(str(csv_path)))

    assert len(rows) == 2
    assert rows[0].username == "alice"
    assert rows[0].disabled is True
    assert rows[0].domain_admin is False
    assert rows[1].username == "admin1"
    assert rows[1].disabled is False
    assert rows[1].domain_admin is True


def test_read_snapshot_does_not_filter_disabled_accounts(tmp_path):
    # Disabled accounts must be kept -- the diff needs disabled status on
    # both sides.
    csv_path = tmp_path / "snapshot.csv"
    csv_path.write_text(
        CSV_HEADER
        + "2020-01-01T00:00:00+00:00,true,Bob B,CN=bob,example.com,false,"
        "bob@example.com,,2020-01-01T00:00:00+00:00,bob\n",
        encoding="utf-8",
    )

    rows = list(snapshot_source.read_snapshot(str(csv_path)))

    assert len(rows) == 1
    assert rows[0].username == "bob"
    assert rows[0].disabled is True


def test_read_snapshot_skips_malformed_row_and_keeps_the_rest(tmp_path, monkeypatch, capsys):
    # A row that raises while being turned into a SnapshotRecord (here
    # simulated by making SnapshotRecord construction blow up for one
    # username) must not take down the rest of the dump.
    csv_path = tmp_path / "snapshot.csv"
    csv_path.write_text(
        CSV_HEADER
        + "2020-01-01T00:00:00+00:00,false,Alice A,CN=alice,example.com,false,"
        "alice@example.com,,2020-01-01T00:00:00+00:00,alice\n"
        + "2020-01-01T00:00:00+00:00,false,Bob B,CN=bob,example.com,false,"
        "bob@example.com,,2020-01-01T00:00:00+00:00,bob\n"
        + "2020-01-01T00:00:00+00:00,false,Carol C,CN=carol,example.com,false,"
        "carol@example.com,,2020-01-01T00:00:00+00:00,carol\n",
        encoding="utf-8",
    )

    real_record = snapshot_source.SnapshotRecord

    def flaky_record(*args, **kwargs):
        if kwargs.get("username") == "bob":
            raise ValueError("simulated bad row")
        return real_record(*args, **kwargs)

    monkeypatch.setattr(snapshot_source, "SnapshotRecord", flaky_record)

    rows = list(snapshot_source.read_snapshot(str(csv_path)))

    assert [row.username for row in rows] == ["alice", "carol"]
    err = capsys.readouterr().err
    assert "bob" in err
    assert "simulated bad row" in err
