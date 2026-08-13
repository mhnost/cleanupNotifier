"""
Entry point for the deactivation-tracking pipeline (the active design,
see CONTEXT.md): reads the previous and current AD CSV snapshots (both
delivered together each run -- this agent never stores either one),
diffs them, and writes a per-run CSV report of newly-deactivated
accounts (username + timestamp only, no other user attributes).

Requires the PREVIOUS_USER_SOURCE_CSV_PATH and CURRENT_USER_SOURCE_CSV_PATH
environment variables (see config.py).

main() also runs the shared circuit-breaker check (see
circuit_breaker.py) before writing the report -- an implausibly
large fraction of newly-deactivated accounts likely means a bad
snapshot, not a real mass-deactivation event.
"""

import csv
import sys
from datetime import datetime, timezone

import circuit_breaker
import config
import email_relay
import snapshot_source
from snapshot_diff import find_newly_deactivated


def run() -> list[str]:
    return _run_with_total_candidates()[0]


def _run_with_total_candidates() -> tuple[list[str], int]:
    """Same as run(), but also returns the total candidate account count
    (current accounts excluding domain_admin) -- main() needs it for the
    circuit-breaker fraction; run() stays a plain list for callers/tests
    that only care about the newly-deactivated usernames.
    """
    config.require_snapshot_paths()
    previous = list(snapshot_source.read_snapshot(config.PREVIOUS_USER_SOURCE_CSV_PATH))
    current = list(snapshot_source.read_snapshot(config.CURRENT_USER_SOURCE_CSV_PATH))
    newly_deactivated = find_newly_deactivated(previous, current)
    total_candidates = sum(1 for record in current if not record.domain_admin)
    return newly_deactivated, total_candidates


def write_report(usernames: list[str], path: str) -> None:
    now = datetime.now(tz=timezone.utc).isoformat()
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["username", "timestamp"])
        writer.writeheader()
        for username in usernames:
            writer.writerow({"username": username, "timestamp": now})


def main() -> None:
    usernames, total_candidates = _run_with_total_candidates()
    try:
        circuit_breaker.raise_if_tripped(
            flagged_count=len(usernames),
            total_count=total_candidates,
            threshold=config.CIRCUIT_BREAKER_MAX_FRACTION,
            min_sample_size=config.CIRCUIT_BREAKER_MIN_SAMPLE_SIZE,
            label="deactivation-diff pipeline",
        )
    except circuit_breaker.CircuitBreakerTripped as exc:
        email_relay.send_circuit_breaker_trip_email(config.ADMIN_SUMMARY_RECIPIENTS, str(exc))
        raise
    write_report(usernames, config.NEW_DEACTIVATIONS_REPORT_CSV_PATH)
    print(f"{len(usernames)} newly-deactivated account(s) found.", file=sys.stderr)
    print(f"Report written to {config.NEW_DEACTIVATIONS_REPORT_CSV_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
