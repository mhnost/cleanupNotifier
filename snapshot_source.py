"""
Snapshot source layer for the deactivation-tracking pipeline.

Reads a single AD CSV dump into SnapshotRecord objects. Both the
previous and current dump (see config.PREVIOUS_USER_SOURCE_CSV_PATH /
config.CURRENT_USER_SOURCE_CSV_PATH) are read with this same function --
snapshot_diff.find_newly_deactivated() is what compares the two.

Disabled accounts are NOT filtered out here -- disabled status is
exactly what this pipeline needs on both sides of the diff.

One malformed row must not crash the whole run: a bad
row is skipped and logged with its line number and username (if
readable) instead of raising out of read_snapshot() and losing the
rest of the dump.
"""

import csv
import sys
from typing import Iterator

import config
from snapshot_diff import SnapshotRecord


def _parse_bool(value: str) -> bool:
    return (value or "").strip().lower() in ("true", "1", "yes")


def read_snapshot(path: str) -> Iterator[SnapshotRecord]:
    """Read one AD CSV dump and yield a SnapshotRecord per row.

    Skips (and logs to stderr) any row that fails to parse, rather than
    letting one bad row abort the entire snapshot read.
    """
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for line_number, row in enumerate(reader, start=2):  # header is line 1
            try:
                yield SnapshotRecord(
                    username=row.get(config.IDENTIFIER_FIELD),
                    disabled=_parse_bool(row.get(config.DISABLED_FIELD)),
                    domain_admin=_parse_bool(row.get(config.DOMAIN_ADMIN_FIELD)),
                )
            except Exception as exc:
                print(
                    f"WARNING: skipping malformed row {line_number} in {path} "
                    f"(username={row.get(config.IDENTIFIER_FIELD)!r}): {exc}",
                    file=sys.stderr,
                )
