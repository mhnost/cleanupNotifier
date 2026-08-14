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
   (`email_notification_report.py`) all exist and are fully approved.
   Live sending is gated by two independent flags —
   `config.WARNING_DELIVERY_ENABLED` and
   `config.DEACTIVATE_DELIVERY_ENABLED`, both still `False` — so
   warning emails can go live first, while deactivation-notice emails
   stay dry-run until warnings are vetted. See "Picking this back up"
   below for the confirmed rollout order.

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
| `inactivity_logic.py` | Pure per-user classification logic (`classify_account`) — decides `ok`/`warning`/`deactivate`/`excluded`/`review` from days-since-last-activity. 180/187-day thresholds confirmed apt. |
| `user_source.py` | Reads `config.CURRENT_USER_SOURCE_CSV_PATH` into `CandidateUser`s (`fetch_raw_user_records`, `to_candidate_user`) — `last_logon`/`created` fallback, `domain_admin` exclusion, `display_name`, plus `last_logon`/`reference_date` for the email drafts. Skips and logs any row that fails to parse rather than crashing the whole read. |
| `email_relay.py` | SMTP relay + email content: `send_email`/`send_warning_email`/`send_deactivation_notice_email`, `build_warning_body`/`build_deactivation_notice_body` (greeting by `display_name`, last logon date, deadline date, domain via `DOMAIN_DISPLAY_NAMES`), plus `build_summary_body`/`send_summary_email` for the per-run admin summary. |
| `email_notification_report.py` | Entry point wiring classification to the relay (`classify_all()` + `dispatch()`), including per-account `deadline_date`/`display_name`. Warning and deactivation-notice outcomes are gated independently by `config.WARNING_DELIVERY_ENABLED`/`config.DEACTIVATE_DELIVERY_ENABLED` (both default `False`) — currently dry-run only. `main()` also runs the circuit-breaker check before dispatching, and always sends a counts-only admin summary to `config.ADMIN_SUMMARY_RECIPIENTS`. |
| `circuit_breaker.py` | Pure fraction check (`check()`/`raise_if_tripped()`, plus `check_per_domain()`/`format_domain_trip_message()`) shared by both entry points' `main()` — evaluated **per domain** (PMO, 2026-08-14), so a domain with an implausibly large flagged fraction (`config.CIRCUIT_BREAKER_MAX_FRACTION`/`CIRCUIT_BREAKER_MIN_SAMPLE_SIZE`) is excluded from that run's report/dispatch instead of blocking every other domain. Both `main()`s email a trip notification to `config.ADMIN_SUMMARY_RECIPIENTS` via `email_relay.send_circuit_breaker_trip_email()` whenever any domain trips, and still abort entirely if every domain with candidates trips. |
| `tests/` | Unit tests (`test_snapshot_diff.py`, `test_snapshot_source.py`, `test_deactivation_report.py`, `test_email_relay.py`, `test_inactivity_logic.py`, `test_user_source.py`, `test_email_notification_report.py`, `test_circuit_breaker.py`, 79 tests). |

`dry_run_report.py` (the old single-CSV entry point that tied
classification, reporting, and audit-logging together in one script)
remains deleted — its role is now split between `deactivation_report.py`
(deactivation tracking) and `email_notification_report.py`
(classification + send, currently dry-run only).

## Setup

```
pip install -r requirements.txt
pytest                     # run the test suite -- should show 79 passed
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

Both CSVs are expected to have columns: `created`, `disabled`,
`display_name`, `dn`, `domain`, `domain_admin`, `email`, `last_logon`,
`password_last_set`, `username`. Output is `new_deactivations_report.csv`
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
this back up" below for the confirmed rollout order.

## Picking this back up

Both flags are plain Python booleans hardcoded in `config.py` (not env
vars) — changing stage means editing that file and redeploying, not
flipping a runtime setting, so who-changed-what-when shows up in
version history. To check which stage is currently live, read those
two lines in `config.py` directly, or run:

```
python -c "import config; print(config.WARNING_DELIVERY_ENABLED, config.DEACTIVATE_DELIVERY_ENABLED)"
```

Confirmed rollout order (PMO/IT):

| Stage | `WARNING_DELIVERY_ENABLED` | `DEACTIVATE_DELIVERY_ENABLED` |
|---|---|---|
| Dry run | `False` | `False` |
| Live warnings only | `True` | `False` |
| Fully live | `True` | `True` |

1. Pre-flight before the dry run: re-check `config.CIRCUIT_BREAKER_MAX_FRACTION`/
   `MIN_SAMPLE_SIZE` still make sense against the current account
   population **per domain** (the breaker is evaluated per domain, not
   org-wide — PMO, 2026-08-14, since domains are inspected one at a
   time across the org's 11 domains), and confirm the admin summary +
   circuit-breaker-trip emails land correctly at
   `config.ADMIN_SUMMARY_RECIPIENTS`.
2. Run dry — both flags `False` — long enough to confirm the weekly
   warned/notified/missing-email counts look plausible and stable,
   with no circuit-breaker trips.
3. Flip `WARNING_DELIVERY_ENABLED = True` only once warning-email
   sending is explicitly signed off; leave
   `DEACTIVATE_DELIVERY_ENABLED = False` until those warnings have
   been vetted with real recipients.
4. Flip `DEACTIVATE_DELIVERY_ENABLED = True` once deactivation-notice
   sending is separately signed off. To roll back a stage, flip the
   relevant flag back to `False` and redeploy — no other cleanup is
   needed (no per-user state is ever persisted, per the GDPR
   constraints in `CONTEXT.md`).

## Scheduling weekly runs

Nothing in this repo triggers a run automatically — no scheduled task,
cron job, or CI pipeline is set up yet. This is a required setup step,
not something already wired up. On Windows, a Task Scheduler action
per weekly run needs to:

1. Deliver fresh `previous`/`current` AD CSV snapshots to wherever the
   task will read them from (this agent never fetches or stores them
   itself — that's a separate upstream responsibility).
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
