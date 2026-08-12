# Deactivation Tracking Agent

This agent never deactivates an account itself — a separate,
already-existing system owns deactivation end to end. It has two jobs:
1. **Deactivation tracking:** compare two AD user CSV dumps (a previous
   snapshot and the current one), diff them, and report any accounts
   that newly became deactivated. Both snapshots are delivered together
   on every run — the agent never stores either one.
2. **End-user emails** (reverted back into scope 2026-07-23): send a
   warning email and a deactivation-notification email to affected end
   users, via an SMTP relay. Classification (`inactivity_logic.py`,
   `user_source.py`), the relay (`email_relay.py`), and the wiring
   between them (`email_notification_report.py`) all exist now.
   **Rollout confirmed by PMO/IT (2026-08-12, see `ROLLOUT.md`):** live
   sending is gated by two independent flags —
   `config.WARNING_DELIVERY_ENABLED` and
   `config.DEACTIVATE_DELIVERY_ENABLED`, both still `False` — so
   warning emails can go live first (Phase 2) while deactivation-notice
   emails stay dry-run until Phase 3. See `CONTEXT.md`
   "Wiring: email_notification_report.py".

**Start here:**
- [`CONTEXT.md`](CONTEXT.md) — full design doc and decisions made,
  including what's superseded and why.
- [`TODO.md`](TODO.md) — the **only** place unanswered questions and
  pending implementation work are tracked. Check it before assuming
  anything is still blocked or still unimplemented.
- [`ROLLOUT.md`](ROLLOUT.md) — the phased rollout plan (dry-run → live
  warnings → live deactivation-notices), **confirmed by PMO/IT
  2026-08-12**. The two-flag split it called for is now built; flipping
  `WARNING_DELIVERY_ENABLED` to `True` to actually enter Phase 2 is
  still a separate, explicit step (see "Picking this back up" below).

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
| `circuit_breaker.py` | Pure fraction check (`check()`/`raise_if_tripped()`) shared by both entry points' `main()` — aborts a run if an implausibly large fraction of accounts are flagged (`config.CIRCUIT_BREAKER_MAX_FRACTION`/`CIRCUIT_BREAKER_MIN_SAMPLE_SIZE`), instead of writing a suspicious report or sending a wave of emails. Both `main()`s also email a trip notification to `config.ADMIN_SUMMARY_RECIPIENTS` via `email_relay.send_circuit_breaker_trip_email()`. |
| `tests/` | Unit tests (`test_snapshot_diff.py`, `test_snapshot_source.py`, `test_deactivation_report.py`, `test_email_relay.py`, `test_inactivity_logic.py`, `test_user_source.py`, `test_email_notification_report.py`, `test_circuit_breaker.py`, 73 tests). |

`dry_run_report.py` (the old single-CSV entry point that tied
classification, reporting, and audit-logging together in one script)
remains deleted — its role is now split between `deactivation_report.py`
(deactivation tracking) and `email_notification_report.py`
(classification + send, currently dry-run only).

## Running (deactivation tracking)

```
pip install -r requirements.txt
pytest                     # run the test suite
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
wiring the pieces together. Every other prerequisite is resolved (see
`TODO.md`), and the phased rollout plan in `ROLLOUT.md` is now
confirmed by PMO/IT (2026-08-12) — Phase 2 is warnings only
(`WARNING_DELIVERY_ENABLED=True`, `DEACTIVATE_DELIVERY_ENABLED`
stays `False`); Phase 3 flips the second flag once Phase 2 is vetted.

## Picking this back up

1. Read `ROLLOUT.md` — the rollout order is confirmed and the flag
   split it called for is built. Its "Quick reference: flag state per
   phase" table shows exactly which of `WARNING_DELIVERY_ENABLED` /
   `DEACTIVATE_DELIVERY_ENABLED` should be `True`/`False` for each
   phase, and how to check which phase is currently live, advance to
   the next one, or roll back — both flags are hardcoded booleans in
   `config.py` (not env vars), so changing phase means editing that
   file and redeploying, not flipping a runtime setting.
2. Don't flip either flag ahead of its phase's "Owner sign-off" line
   in `ROLLOUT.md`, regardless of how complete the wiring and content
   look.
