# AD Deactivation Agent — Design & Decisions

## Purpose
Two jobs, neither of which disables an account — deactivation itself is
owned end-to-end by a separate, already-existing system:
1. **Deactivation tracking:** diff two AD user CSV snapshots, report
   accounts that newly became deactivated.
2. **End-user email notifications:** send a warning email and, later, a
   deactivation-notification email to affected end users via an SMTP
   relay, based on per-account inactivity classification.

## Current State
Both pipelines are fully built and tested. The diff pipeline runs live
and unblocked. The email pipeline is fully wired and its SMTP relay is
live-verified; email copy, domain-name mapping, and delivery policy
are all finalized. Live sending is gated by two independent flags,
`config.WARNING_DELIVERY_ENABLED` and
`config.DEACTIVATE_DELIVERY_ENABLED` (both still `False`) — warnings
go live first, deactivation-notices later, per a staged rollout. See
README.md "Picking this back up" for how to advance or roll back a
stage.

## Deactivation Tracking (Diff Pipeline)
- **Inputs:** two CSVs, same schema (`created`, `disabled`,
  `display_name`, `dn`, `domain`, `domain_admin`, `email`,
  `last_logon`, `password_last_set`, `username`), exported from
  Grafana — a previous and current snapshot, delivered fresh together
  every run. Neither is stored between runs (see "GDPR Constraints"
  below).
- **Match key:** `username` (stable, unique across snapshots).
- **"Newly deactivated":** present in both dumps with `disabled`
  transitioning `false` → `true`, excluding `domain_admin` accounts —
  every service account is also flagged `domain_admin`, so that one
  field is sufficient to exclude both admins and service accounts.
- Accounts vanishing entirely from the current dump (rather than being
  disabled) aren't expected to occur and have no special handling.
- **Output:** `new_deactivations_report.csv`, one row per
  newly-deactivated account, `username` + `timestamp` only. No admin
  email for this pipeline. Kept indefinitely — no retention policy
  needed.
- **Cadence:** weekly, matching the CSV refresh.
- **Modules:** `snapshot_diff.py` (pure diff logic), `snapshot_source.py`
  (CSV reader — skips and logs any row that fails to parse rather than
  crashing the run), `deactivation_report.py` (entry point, `main()`).

## Email Notification Pipeline
- **Classification** (`inactivity_logic.classify_account`):
  `days_since_reference` (last_logon, falling back to `created` if the
  account never logged in) against `WARNING_MIN_DAYS`=180,
  `WARNING_MAX_DAYS`=186, `DEACTIVATE_MIN_DAYS`=187 → outcome
  `OK`/`WARNING`/`DEACTIVATE`/`EXCLUDED`/`REVIEW`. These thresholds
  are aligned with the separate deactivation system's actual trigger
  point.
- **Source** (`user_source.py`): reads `config.CURRENT_USER_SOURCE_CSV_PATH`
  (the same file the diff pipeline uses for "current"), excludes
  `domain_admin` accounts, carries `display_name`, `last_logon`,
  `reference_date`, and `domain` per account.
- **Email content** (`email_relay.py`):
  - Greeting uses `display_name` (falls back to a name-less greeting
    if blank).
  - Domain is shown via `DOMAIN_DISPLAY_NAMES` — a friendly-name
    mapping over the raw AD/dashboard domain value (see the module for
    the full current domain list), falling back to the raw value if
    unmapped and to `DOMAIN_PLACEHOLDER` only if blank.
  - Body states the last-logon date (or "no logon on record") and a
    concrete deadline date (`reference_date + DEACTIVATE_MIN_DAYS`
    days).
  - Both structure and wording are finalized.
- **Wiring** (`email_notification_report.py`): `classify_all()` +
  `dispatch()`. Accounts with an actionable outcome but no email on
  file have their notification skipped (not guessed at), but are still
  counted rather than silently dropped. Actual sending is gated
  per-outcome by `config.WARNING_DELIVERY_ENABLED` /
  `config.DEACTIVATE_DELIVERY_ENABLED` (both default `False`, split
  2026-08-12 per the staged rollout plan) — while a flag is off,
  `dispatch()` only records `"would_send"` for that outcome.
- **Admin run summary:** every run emails
  `config.ADMIN_SUMMARY_RECIPIENTS` (onost@eg.no, nishh@eg.dk) a
  counts-only summary (warned / notified / missing-email), regardless
  of either delivery flag. Deliberately excludes usernames or other
  per-account PII — the summary's purpose only needs counts, and email
  is a less access-controlled, less retention-managed channel than the
  diff pipeline's report file.
- **SMTP relay** (`email_relay.send_email`): `postal.egcloud.no`:25,
  credential `egnorway/user-cleanup`, TLS off, From address
  `noreply@egcloud.no` — the relay allow-lists the From address per
  service account (two other addresses were rejected by the relay).
  All values load from `ad_deactivation_agent/.env` via
  `python-dotenv`. Live-verified with real smoke-test sends.
- **Rate limits / bounces:** no throttling, backoff, or bounce-tracking
  in v1 — a deliberate simplification; "relay accepted" counts as
  "notified."
- **Duplicate sends:** no per-user state exists to prevent repeat
  warning emails across weekly runs while an account sits in the
  180–186 day window (persisting that state would conflict with the
  GDPR no-storage stance below). Accepted as a non-issue at true
  weekly cadence.

## Circuit Breaker (`circuit_breaker.py`)
Shared by both pipelines' `main()`, checked **per domain** rather than
once across the whole snapshot (domains are inspected one at a time
operationally, across 11 domains of roughly egrdrift's size, so one
domain's bad snapshot shouldn't block every other domain's
report/dispatch). For each domain, if its flagged/total
fraction exceeds `config.CIRCUIT_BREAKER_MAX_FRACTION` (50%) on a
sample of at least `config.CIRCUIT_BREAKER_MIN_SAMPLE_SIZE` (20)
accounts, that domain's accounts are excluded from this run's
report/dispatch and a trip notification is emailed to
`config.ADMIN_SUMMARY_RECIPIENTS` via
`email_relay.send_circuit_breaker_trip_email()` — other domains still
proceed normally. If every domain with candidates trips (nothing left
to report/dispatch), the run still raises `CircuitBreakerTripped` and
aborts entirely, same as the original whole-run behavior. Guards
against a bad CSV dump being misread as a mass deactivation event.

**First-run mode**: real per-domain fractions checked 2026-08-14
ranged ~1%–74% — 3 of the 11 domains (egrdrift,
egrtest, trygg2000) exceed the normal 50% threshold on the very first
backlog-clearing run, which reflects genuine years-old backlog, not a
bad snapshot. `config.circuit_breaker_threshold()` returns
`CIRCUIT_BREAKER_FIRST_RUN_MAX_FRACTION` (default 80%) instead of the
normal `CIRCUIT_BREAKER_MAX_FRACTION` when `FIRST_RUN_MODE=true` is
set as an environment variable for that one invocation — deliberately
env-var-opt-in rather than a `config.py` edit, so it can't silently
stay elevated for a later, ordinary weekly run.

## GDPR Constraints
- No persistent storage of user data beyond a single run's processing:
  both snapshots are supplied fresh each run and never retained.
- Logging and reporting are minimal by design: the diff report and
  email-pipeline records carry `username`(+`timestamp`) or counts
  only — no other per-account attributes are logged or emailed in
  bulk anywhere in the project.
- These constraints are why there's no dedupe-on-rerun state and no
  bounce-tracking — both would require persisting per-user data
  between runs.

## What's Left
Every design question is resolved. What remains is executing the
staged rollout: a pre-flight check, a dry-run period, then flipping
`config.WARNING_DELIVERY_ENABLED = True` once warnings are ready to
go live, and `config.DEACTIVATE_DELIVERY_ENABLED = True` once
deactivation-notices are too. See README.md "Picking this back up".
