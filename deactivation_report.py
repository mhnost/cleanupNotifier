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


def _read_snapshots() -> tuple[list, list]:
    config.require_snapshot_paths()
    previous = list(snapshot_source.read_snapshot(config.PREVIOUS_USER_SOURCE_CSV_PATH))
    current = list(snapshot_source.read_snapshot(config.CURRENT_USER_SOURCE_CSV_PATH))
    return previous, current


def run() -> list[str]:
    previous, current = _read_snapshots()
    return find_newly_deactivated(previous, current)


def write_report(usernames: list[str], path: str) -> None:
    now = datetime.now(tz=timezone.utc).isoformat()
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["username", "timestamp"])
        writer.writeheader()
        for username in usernames:
            writer.writerow({"username": username, "timestamp": now})


def main() -> None:
    """Runs the circuit-breaker check per domain (PMO, 2026-08-14) rather
    than once for the whole snapshot -- domains are inspected one at a
    time, so a bad snapshot for one domain shouldn't block every other
    domain's report. A domain that trips is excluded from this run's
    report and reported via the admin trip email; domains that don't
    trip proceed normally. If every domain with candidates trips (so
    there's nothing left to report), the run still aborts without
    writing, same as before this change.
    """
    previous, current = _read_snapshots()
    newly_deactivated_set = set(find_newly_deactivated(previous, current))

    candidates_by_domain: dict[str, list] = {}
    for record in current:
        if record.domain_admin:
            continue
        candidates_by_domain.setdefault(record.domain, []).append(record)

    counts_by_domain = {
        domain: (
            sum(1 for record in records if record.username in newly_deactivated_set),
            len(records),
        )
        for domain, records in candidates_by_domain.items()
    }
    results_by_domain = circuit_breaker.check_per_domain(
        counts_by_domain,
        threshold=config.circuit_breaker_threshold(),
        min_sample_size=config.CIRCUIT_BREAKER_MIN_SAMPLE_SIZE,
    )
    tripped = {domain: result for domain, result in results_by_domain.items() if result.tripped}

    usernames = [
        record.username
        for domain, records in candidates_by_domain.items()
        if domain not in tripped
        for record in records
        if record.username in newly_deactivated_set
    ]

    if tripped:
        message = circuit_breaker.format_domain_trip_message("deactivation-diff pipeline", tripped)
        email_relay.send_circuit_breaker_trip_email(config.ADMIN_SUMMARY_RECIPIENTS, message)
        if not usernames:
            raise circuit_breaker.CircuitBreakerTripped(message)

    write_report(usernames, config.NEW_DEACTIVATIONS_REPORT_CSV_PATH)
    if config.FIRST_RUN_MODE:
        print(
            f"FIRST_RUN_MODE: circuit breaker threshold elevated to "
            f"{config.CIRCUIT_BREAKER_FIRST_RUN_MAX_FRACTION:.0%} for this run.",
            file=sys.stderr,
        )
    print(f"{len(usernames)} newly-deactivated account(s) found.", file=sys.stderr)
    if tripped:
        print(
            f"WARNING: circuit breaker tripped for {len(tripped)} domain(s), excluded "
            f"from this run's report: {', '.join(tripped)}",
            file=sys.stderr,
        )
    print(f"Report written to {config.NEW_DEACTIVATIONS_REPORT_CSV_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
