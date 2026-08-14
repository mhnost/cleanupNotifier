# Deactivation Tracking Agent

This agent never deactivates an account itself — a separate,
already-existing system owns deactivation end to end. It has two jobs:
1. **Deactivation tracking:** compare two AD user CSV dumps (a previous
   snapshot and the current one), diff them, and report any accounts
   that newly became deactivated. Both snapshots are delivered together
   on every run — the agent never stores either one.
2. **End-user emails:** send a warning email and a
   deactivation-notification email to affected end users, via an SMTP
   relay. Classification (`inactivity_logic.py`, `user_source.py`),
   the relay (`email_relay.py`), and the wiring between them
   (`email_notification_report.py`) all exist and are complete.
   Live sending is gated by two independent flags —
   `config.WARNING_DELIVERY_ENABLED` and
   `config.DEACTIVATE_DELIVERY_ENABLED`, both still `False` — so
   warning emails can go live first, while deactivation-notice emails
   stay dry-run until warnings are reviewed. See "Picking this back up"
   below for the rollout order.

**Start here:**
- [`CONTEXT.md`](CONTEXT.md) — the design doc: how each pipeline
  works and why the key constraints exist.

## Layout

| File | Role |
|---|---|
| `config.py` | Field names, `SERVICE_ACCOUNT_MARKING_CONFIRMED`, snapshot CSV paths, report output path, SMTP relay config (loaded from `.env` via `python-dotenv`), `ADMIN_SUMMARY_RECIPIENTS`. |
| `snapshot_diff.py` | Pure diff logic (`find_newly_deactivated`) — decides which accounts count as newly deactivated. No I/O; the most heavily tested piece. |
| `snapshot_source.py` | Reads one AD CSV dump into `SnapshotRecord`s (`read_snapshot()`), keeping disabled accounts (needed for the diff). Skips and logs any row that fails to parse rather than crashing the whole read. |
| `deactivation_report.py` | Entry point for deactivation tracking. Reads the previous + current CSV dumps, diffs them, writes `new_deactivations_report.csv` (username + timestamp only). `main()` runs the circuit-breaker check before writing. |
| `inactivity_logic.py` | Pure per-user classification logic (`classify_account`) — decides `ok`/`warning`/`deactivate`/`excluded`/`review` from days-since-last-activity. 180/187-day thresholds. |
| `user_source.py` | Reads `config.CURRENT_USER_SOURCE_CSV_PATH` into `CandidateUser`s (`fetch_raw_user_records`, `to_candidate_user`) — `last_logon`/`created` fallback, `domain_admin` exclusion, `display_name`, plus `last_logon`/`reference_date` for the email drafts. Skips and logs any row that fails to parse rather than crashing the whole read. |
| `email_relay.py` | SMTP relay + email content: `send_email`/`send_warning_email`/`send_deactivation_notice_email`, `build_warning_body`/`build_deactivation_notice_body` (greeting by `display_name`, last logon date, deadline date, domain via `DOMAIN_DISPLAY_NAMES`), plus `build_summary_body`/`send_summary_email` for the per-run admin summary. |
| `email_notification_report.py` | Entry point wiring classification to the relay (`classify_all()` + `dispatch()`), including per-account `deadline_date`/`display_name`. Warning and deactivation-notice outcomes are gated independently by `config.WARNING_DELIVERY_ENABLED`/`config.DEACTIVATE_DELIVERY_ENABLED` (both default `False`) — currently dry-run only. `main()` also runs the circuit-breaker check before dispatching, and always sends a counts-only admin summary to `config.ADMIN_SUMMARY_RECIPIENTS`. |
| `circuit_breaker.py` | Pure fraction check (`check()`/`raise_if_tripped()`, plus `check_per_domain()`/`format_domain_trip_message()`) shared by both entry points' `main()` — evaluated **per domain**, so a domain with an implausibly large flagged fraction (`config.CIRCUIT_BREAKER_MAX_FRACTION`/`CIRCUIT_BREAKER_MIN_SAMPLE_SIZE`) is excluded from that run's report/dispatch instead of blocking every other domain. Both `main()`s email a trip notification to `config.ADMIN_SUMMARY_RECIPIENTS` via `email_relay.send_circuit_breaker_trip_email()` whenever any domain trips, and still abort entirely if every domain with candidates trips. |
| `tests/` | Unit tests (`test_snapshot_diff.py`, `test_snapshot_source.py`, `test_deactivation_report.py`, `test_email_relay.py`, `test_inactivity_logic.py`, `test_user_source.py`, `test_email_notification_report.py`, `test_circuit_breaker.py`, `test_config.py`, 82 tests). |

`dry_run_report.py` (the old single-CSV entry point that tied
classification, reporting, and audit-logging together in one script)
remains deleted — its role is now split between `deactivation_report.py`
(deactivation tracking) and `email_notification_report.py`
(classification + send, currently dry-run only).

## Setup

```
pip install -r requirements.txt
pytest                     # run the test suite -- should show 82 passed
cp .env.example .env       # then fill in the real SMTP relay values
```

`.env` is gitignored and never committed — it's the only place SMTP
credentials live (loaded via `python-dotenv`, see `config.py`). Only
needed once a delivery flag is `True`; the deactivation-tracking
pipeline below doesn't touch SMTP at all.

## Running (deactivation tracking)

```
export PREVIOUS_USER_SOURCE_CSV_PATH=/path/to/previous.csv   # (or `set` on Windows cmd)
export CURRENT_USER_SOURCE_CSV_PATH=/path/to/current.csv
python deactivation_report.py
```

Runs weekly, matching the CSV dump refresh cycle. Report files
(`new_deactivations_report.csv`) are safe to keep indefinitely — no
rotation/retention policy is needed.

Both CSVs are dumps exported from Grafana. They're expected to have
columns: `created`, `disabled`, `display_name`, `dn`, `domain`,
`domain_admin`, `email`, `last_logon`, `password_last_set`,
`username`. Output is `new_deactivations_report.csv`
(override via `NEW_DEACTIVATIONS_REPORT_CSV_PATH`) — one row per
newly-deactivated account, `username` + `timestamp` only.

## Running (email notifications — dry run only)

```
export CURRENT_USER_SOURCE_CSV_PATH=/path/to/current.csv
export SMTP_RELAY_HOST=relay.example.com        # only needed once a delivery flag is True
export SMTP_FROM_ADDRESS=noreply@example.com
python email_notification_report.py
```

Classifies every account and prints what it *would* send — no email is
actually delivered while `config.WARNING_DELIVERY_ENABLED` and
`config.DEACTIVATE_DELIVERY_ENABLED` are both `False` (the default).
To preview the draft email copy directly:

```
python -c "
import email_relay
from datetime import datetime, timezone
print(email_relay.build_warning_body(datetime(2026,1,10,tzinfo=timezone.utc), datetime(2026,7,15,tzinfo=timezone.utc)))
"
```

Flipping either flag to `True` is a separate, explicit decision from
wiring the pieces together. Every prerequisite (email copy, domain
mapping, thresholds, admin recipients) is resolved — see "Picking
this back up" below for the rollout order.

## Picking this back up

Both flags are plain Python booleans hardcoded in `config.py` (not env
vars) — changing stage means editing that file and redeploying, not
flipping a runtime setting, so who-changed-what-when shows up in
version history. To check which stage is currently live, read those
two lines in `config.py` directly, or run:

```
python -c "import config; print(config.WARNING_DELIVERY_ENABLED, config.DEACTIVATE_DELIVERY_ENABLED)"
```

Rollout order:

| Stage | `WARNING_DELIVERY_ENABLED` | `DEACTIVATE_DELIVERY_ENABLED` |
|---|---|---|
| Dry run | `False` | `False` |
| Live warnings only | `True` | `False` |
| Fully live | `True` | `True` |

1. Pre-flight before the dry run: re-check `config.CIRCUIT_BREAKER_MAX_FRACTION`/
   `MIN_SAMPLE_SIZE` still make sense against the current account
   population **per domain** (the breaker is evaluated per domain, not
   org-wide, since domains are inspected one at a time across the
   org's 11 domains). Real per-domain fractions checked 2026-08-14
   ranged ~1%–74%; 3 of 11 domains (egrdrift, egrtest, trygg2000) would
   trip the normal 50% threshold on the very first backlog-clearing
   run — that's real accumulated backlog, not a bad snapshot. The
   admin summary + circuit-breaker-trip email path lands correctly at
   `config.ADMIN_SUMMARY_RECIPIENTS`.
1a. **First run only**: set `FIRST_RUN_MODE=true` as an environment
   variable for that one invocation (both entry points read it) to
   raise the ceiling to `config.CIRCUIT_BREAKER_FIRST_RUN_MAX_FRACTION`
   (default `0.8`, override via env var same as the other breaker
   knobs) so the initial backlog clears without tripping. Don't set it
   for any run after that — ordinary weekly runs should use the normal
   50% threshold, since a real steady-state run tripping it is exactly
   the signal the breaker exists to catch.
2. Run dry — both flags `False` — long enough to confirm the weekly
   warned/notified/missing-email counts look plausible and stable,
   with no circuit-breaker trips.
3. Flip `WARNING_DELIVERY_ENABLED = True` once warning-email sending
   is ready to go live; leave `DEACTIVATE_DELIVERY_ENABLED = False`
   until those warnings have been reviewed with real recipients.
4. Flip `DEACTIVATE_DELIVERY_ENABLED = True` once deactivation-notice
   sending is ready to go live too. To roll back a stage, flip the
   relevant flag back to `False` and redeploy — no other cleanup is
   needed (no per-user state is ever persisted, per the GDPR
   constraints in `CONTEXT.md`).

## Scheduling weekly runs

Nothing in this repo triggers a run automatically — no scheduled task,
cron job, or CI pipeline is set up. **This is a required setup step
the incoming owner still needs to do, not something already wired
up.**

There's also an open design gap this setup needs to resolve first:
this pipeline needs both a `previous` and a `current` CSV every run,
but nothing yet defines how last week's `current` becomes this week's
`previous`. Two ways to close that gap:
- **A wrapper script keeps one rotating local copy.** Before each run,
  it copies whatever it saved as "current" last time into "previous,"
  then treats the newly-exported Grafana dump as the new "current."
  Only one snapshot is ever retained between runs (not a history), so
  this stays consistent with the project's GDPR no-retention stance.
- **Whoever exports from Grafana hands over both files every week** —
  the fresh dump plus whatever they kept from last time. This keeps
  the task itself completely stateless, at the cost of relying on a
  manual/external process to retain that one prior file correctly.

Either way, on Windows a Task Scheduler action per weekly run needs
to:

1. Get a fresh `current` AD CSV snapshot (exported from Grafana) and a
   `previous` snapshot resolved per whichever approach above is
   chosen, to wherever the task will read them from — this agent
   never fetches, rotates, or stores them itself.
2. Set `PREVIOUS_USER_SOURCE_CSV_PATH`/`CURRENT_USER_SOURCE_CSV_PATH`
   (and `NEW_DEACTIVATIONS_REPORT_CSV_PATH` if the default output
   location isn't wanted) as environment variables for the task, then
   run `python deactivation_report.py`.
3. Set `CURRENT_USER_SOURCE_CSV_PATH` (SMTP config comes from `.env`,
   not the task's environment) and run `python email_notification_report.py`.
4. Alert on a non-zero exit code from either script — both raise
   `CircuitBreakerTripped` on an abort, which should surface as a
   failed task run, not a silent no-op.

Both scripts are independent processes; running them back-to-back in a
single action (or as two actions in one task) is fine — neither
depends on the other's output.
