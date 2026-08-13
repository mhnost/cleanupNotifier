# Open Questions & TODOs

Single source of truth for unanswered questions and pending implementation
work on this project. This is the **only** place these are tracked — do not
duplicate them in `CONTEXT.md`, `README.md`, or code comments; link back
here instead. See `CONTEXT.md` for full design rationale behind each item.

**2026-07-23: scope changed again.** This agent now only counts/logs
already-performed deactivations by diffing two CSV dumps — it no longer
sends any email or acts on any account. Most of the list below is new;
everything from the previous (email/threshold-based) design has been
moved to "Superseded" at the bottom. See `CONTEXT.md` "New Design:
Snapshot Diff" for the rationale behind each item here.

**2026-07-23, later the same day — key design questions resolved by
PMO**, unblocking implementation (moved to "Resolved" below):
source-of-previous-snapshot, service/admin scoping, deleted-accounts
edge case, log content/destination, and match-key reliability. Cadence
and report retention also confirmed; old per-user pipeline deleted
outright as no longer needed.

**2026-07-23, later still — scope reverted (PMO): end-user warning and
deactivation-notification emails are back**, via SMTP relay. The relay
itself has been sketched (`email_relay.py`, unwired) — see `CONTEXT.md`
"SMTP Relay (Sketch)". This reopens several items previously marked
"Superseded" below (now moved back to "Open Questions").

**2026-07-23, later still — per-user classification logic rebuilt.**
`inactivity_logic.py`/`user_source.py` are back (see `CONTEXT.md`
"Per-User Email Classification"), so "does the classification logic
exist" is no longer an open question — but whether its carried-over
thresholds are the *right* ones still is (Open Question 1 below).

**2026-07-23, later still — wiring stubbed in.**
`email_notification_report.py` now classifies every account and routes
warning/deactivation outcomes toward `email_relay.py`, gated by a new
`config.EMAIL_DELIVERY_ENABLED` flag (default `False`) — see
`CONTEXT.md` "Wiring: email_notification_report.py".

**2026-07-23, later still — thresholds confirmed, email drafts
written.** PMO confirmed the 180/187 thresholds are apt (moved to
"Resolved" below; the 173/180 alternative is no longer under
consideration). `email_relay.py` now builds real draft email bodies
per account (last logon date, deadline date, and a domain-name spot
left as `DOMAIN_PLACEHOLDER`) instead of static placeholder text — see
`CONTEXT.md` "Email Drafts". Content is still unapproved and the
domain-name source is still an open question (Open Question 2 below).

**2026-08-13 — PO sign-off: Open Questions 1–5 all cleared.** Reviewed
with PO in a dedicated meeting; each item below is now marked
resolved inline. Only items 6–12 remain open.

**2026-08-13 — items 6, 8–12 cleared; item 7 is the sole remaining
blocker for handover.** Duplicate-send policy (6), deactivation-timing
coordination with the other system (8), admin DL address (9),
email-pipeline cadence (10), single-combined-job pipeline relationship
(11), and circuit-breaker trip notification routing (12) are all
confirmed. Only item 7 — actually executing Phase 0/1 of `ROLLOUT.md`
and getting Phase-2 sign-off — remains, and is deliberately deferred
to just before handover rather than done now.

## Open Questions (need an answer from the team / IT / CloudOps / DPO)

**2026-07-29 — strawman proposals drafted for the 2026-07-30 meeting.**
Each item below now has a **Proposed:** line — a concrete default to
bring to the meeting so the team is reacting to something instead of
starting blank. None of these are decided; they're starting points.
Items 3 and 8 are flagged as genuine external facts (only IT/the other
system's owner has the real answer) rather than defaults to bless.

1. **Warning email template/content and deactivation-notification email
   template/content — approved (PO, 2026-08-13).** The structure
   drafted in `email_relay.build_warning_body`/
   `build_deactivation_notice_body` (last logon date or "no logon on
   record", a concrete deadline date, one clear CTA, neutral/
   informational tone, helpdesk contact line) is signed off as-is. No
   legal/comms review requested — PO sign-off was sufficient.
2. **Domain name source for the email body — confirmed (PO, 2026-08-13).**
   `email_relay.resolve_domain_display_name()`'s mapping via
   `DOMAIN_DISPLAY_NAMES` (`ad.eg.no` → "EG", `egrtest.no` → "EG Test",
   `egrutv.no` → "EG Utvikling"), including the raw-value and
   `DOMAIN_PLACEHOLDER` fallbacks, is confirmed correct and ready to
   ship live.
3. **SMTP relay connection details — resolved (2026-08-07).** Host
   `postal.egcloud.no`, port 25, service-account credential
   `egnorway/user-cleanup`, `SMTP_USE_TLS=false` (port 25, no
   STARTTLS), From address `noreply@egcloud.no` (confirmed by
   CloudOps after `noreply@eg.no` and `noreply@postal.egcloud.no` were
   both rejected by the relay with `530 From/Sender name is not
   valid` — the relay enforces an allow-listed From address tied to
   the service account, not an arbitrary address). All values now live
   in `ad_deactivation_agent/.env` (loaded via `python-dotenv`, added
   to `config.py` and `requirements.txt`) rather than being exported
   by hand. **Verified live:** a real `send_warning_email()` smoke
   test through this relay was received successfully at mnost@eg.no.
4. **Rate limits and bounce handling — confirmed (PO, 2026-08-13).**
   No throttling/backoff in v1 (synchronous loop is fine at expected
   weekly volume); "relay accepted the send" counts as "notified" —
   bounces are not chased. Revisit only if real volume/bounce rates
   become a problem.
5. **Missing-email safety net — policy reconfirmed (PO, 2026-08-13).**
   `email_notification_report.dispatch()`'s existing behavior — route
   accounts with an actionable outcome but no email on file to a
   separate `missing_email` list for manual review, rather than silent
   skip or silent deactivate-with-no-notice — is confirmed as-is.
6. **Duplicate-send-on-rerun — accepted (PO, 2026-08-13).** Repeat
   warning emails across consecutive weekly runs (while an account
   sits in the 180–186 day window, up to ~7 sends) are intended
   reminder behavior, not a bug — no send-once guarantee needed.
7. **Approval gate for the staged rollout — resolved (PMO/IT, 2026-08-12);
   execution timing confirmed (PO, 2026-08-13).** Rollout order
   confirmed as drafted in `ROLLOUT.md`: dry-run → live warnings only
   → live deactivation-notices, with per-phase sign-off. The
   `WARNING_DELIVERY_ENABLED`/`DEACTIVATE_DELIVERY_ENABLED` flag split
   it required is built (`config.py`, `email_notification_report.py`).
   **The last thing to do before handover:** actually execute Phase
   0/1 and get the Phase-2 sign-off to flip `WARNING_DELIVERY_ENABLED`
   to `True` — see `ROLLOUT.md`. Deliberately left until just before
   handover rather than done now.
8. **Timing coordination with the separate deactivation system —
   confirmed (2026-08-13).** Its deactivation timing aligns with this
   agent's day-187 deactivation-notice point; `config.DEACTIVATE_MIN_DAYS`
   needs no change.
9. **Admin distribution list address — confirmed.** The two recipients
   already smoke-tested in `ROLLOUT.md` Phase 0 (onost@eg.no,
   nishh@eg.dk) are correct for the weekly summary (counts: warned /
   notified / missing-email / circuit-breaker-tripped).
10. **Cadence for the email pipeline — confirmed.** Same weekly cadence
    as the diff/count pipeline, tied to the same CSV refresh.
11. **Relationship between the two pipelines — confirmed: one combined
    weekly job.** The diff/count pipeline (`deactivation_report.py`)
    and the email pipeline run as a single job — diff first, then
    classification+dispatch against the same current-snapshot CSV (the
    previous snapshot is only needed for the diff step).
12. **Circuit-breaker notification routing — confirmed: same recipients
    as the summary email.** A trip notification routes to the same two
    admin-DL addresses confirmed in item 9 (onost@eg.no, nishh@eg.dk),
    not stderr-only.

## TODOs (implementation)

0. **Test debt (2026-08-12) — fixed (2026-08-13).** The two stale
   assertions in `test_email_relay.py`
   (`test_warning_body_greets_by_display_name_when_present`,
   `test_send_warning_email_passes_display_name_through`) expecting
   `"Hello X,"` have been updated to `"Dear X,"` to match
   `email_relay._greeting()`'s actual (and approved) output. All 21
   tests in `test_email_relay.py` pass.

1. **Diff/comparison pipeline — built** (`snapshot_diff.py`,
   `snapshot_source.py`, `deactivation_report.py`). Reads the previous
   and current CSV dumps via `PREVIOUS_USER_SOURCE_CSV_PATH` /
   `CURRENT_USER_SOURCE_CSV_PATH`, matches by `username`, detects
   `disabled` false→true transitions, excludes `domain_admin` accounts,
   and writes `new_deactivations_report.csv` (username + timestamp
   only). No admin-DL email — the report file is the only output.
2. **`dry_run_report.py` (old single-CSV entry point) — stays deleted.**
   It's superseded by the split between `deactivation_report.py`
   (diff/count) and the not-yet-built email entry point. Not rebuilt.
3. **Tests for the diff logic — done**
   (`test_snapshot_diff.py`, `test_snapshot_source.py`,
   `test_deactivation_report.py`, 13 tests, all passing).
4. **SMTP relay — built** (`email_relay.py`: `send_email`,
   `send_warning_email`, `send_deactivation_notice_email`, plus
   `config.py`'s `SMTP_RELAY_HOST`/`SMTP_RELAY_PORT`/`SMTP_USE_TLS`/
   `SMTP_USERNAME`/`SMTP_PASSWORD`/`SMTP_FROM_ADDRESS` and
   `require_smtp_config()`).
5. **Per-user classification logic — rebuilt (2026-07-23).**
   `inactivity_logic.py` (`classify_account`) and `user_source.py`
   (`fetch_raw_user_records`, `to_candidate_user`) restore the
   pre-deletion design, reading `config.CURRENT_USER_SOURCE_CSV_PATH`
   instead of a separate CSV path (reuses the diff pipeline's existing
   "current" snapshot input). Thresholds confirmed unchanged (see
   "Resolved" below). `CandidateUser` also now carries `last_logon` and
   `reference_date` (needed for the email drafts, see item 6 below).
6. **Wiring — stubbed in (2026-07-23).** `email_notification_report.py`
   (`classify_all()` + `dispatch()`) calls `classify_account()` for
   every account, computes a `deadline_date` per account
   (`reference_date + DEACTIVATE_MIN_DAYS` days), and routes `WARNING`
   outcomes to `email_relay.send_warning_email()` / `DEACTIVATE`
   outcomes to `send_deactivation_notice_email()` with `last_logon`/
   `deadline_date` — gated by `config.EMAIL_DELIVERY_ENABLED` (default
   `False`, so it currently only reports `"would_send"` and never calls
   the relay). Manually smoke-tested against a sample CSV — correctly
   flagged a long-inactive account, excluded a `domain_admin` account,
   skipped a recently-active account, and routed a no-email account to
   the missing-email list.
7. **Email drafts written (2026-07-23).** `email_relay.py`'s
   `build_warning_body()`/`build_deactivation_notice_body()` render
   real per-account content: last logon date (or "no logon on record"),
   deadline date, and a domain-name placeholder (`DOMAIN_PLACEHOLDER =
   "[DOMAIN]"`, see Open Question 2). Manually smoke-tested — output
   reads as expected for both the "has logged in before" and "never
   logged in" cases. Still needs: wording approval (Open Question 1)
   and a real domain-name source (Open Question 2) before
   `EMAIL_DELIVERY_ENABLED` can be considered.
8. **Per-record error isolation and circuit breaker — built (2026-07-29).**
   `snapshot_source.read_snapshot()` and `user_source.fetch_raw_user_records()`
   now catch per-row exceptions (e.g. an unparseable date), skip just
   that row, and log it to stderr with its line number and username
   instead of the whole run crashing. `circuit_breaker.py` (pure,
   tested standalone) checks flagged/total fraction against
   `config.CIRCUIT_BREAKER_MAX_FRACTION` (default 0.5) once
   `config.CIRCUIT_BREAKER_MIN_SAMPLE_SIZE` (default 20) accounts are
   present, and both `deactivation_report.main()` and
   `email_notification_report.main()` abort — raising
   `CircuitBreakerTripped`, writing/sending nothing — if it trips.
   **Still open:** the actual threshold/min-sample-size values are
   placeholders pending confirmation, not yet a PMO-approved number.

## Resolved (facts that remain true regardless of this pivot)

- **Backend** — confirmed to be a CSV dump, same schema, for both the
  previous and current snapshot. See `CONTEXT.md` "New Design".
- **Service account marking** — every service account is also flagged
  `domain_admin`, so `domain_admin` alone remains a sufficient signal if
  service accounts need to be filtered out of the new count.
- **Does this agent deactivate accounts?** — No, a separate system does.
  This agent counts/logs already-performed deactivations via CSV diff,
  and (as of the 2026-07-23 reversion) separately sends a
  deactivation-*notification* email — it never disables an account
  itself, in either pipeline.
- **Source of the "previous" snapshot** (PMO, 2026-07-23) — it is
  delivered alongside the current dump on every run; the agent never
  stores either snapshot itself. This fully resolves the earlier GDPR
  tension around retaining state between runs.
- **Service/admin account scoping** (PMO, 2026-07-23) — service and
  admin accounts are always untouched and never actually deactivated by
  the separate system, so they fall out of scope entirely for this
  count; `domain_admin` accounts should be excluded from the diff.
- **Deleted-account edge case** (PMO, 2026-07-23) — accounts vanishing
  entirely from the current dump (as opposed to being disabled) is not
  expected to occur for now; no special handling needed at this time.
- **Match key reliability** (PMO, 2026-07-23) — `username` is stable and
  unique across snapshots; safe to use as the sole match key, no rename
  handling needed.
- **Log content** (PMO, 2026-07-23) — username + timestamp only per
  newly-deactivated account, consistent with this project's existing
  GDPR-minimal logging approach.
- **Output destination** (PMO, 2026-07-23) — a CSV report file per run
  (not an append-only log, not an email). Consumers are the report file
  itself (no active audience yet) and potentially a dashboard — no
  admin-DL email.
- **Cadence** (PMO, 2026-07-23) — confirmed still weekly, matching the
  CSV dump refresh cycle.
- **Report retention** (PMO, 2026-07-23) — no rotation/deletion needed;
  keeping old `new_deactivations_report.csv`-style per-run reports
  indefinitely is not a concern.
- **Inactivity thresholds** (PMO, 2026-07-23) — 180–186 day warning
  window / ≥187 day deactivation-notice point is apt; keep as-is. The
  173/180 alternative reading is no longer under consideration.
- **Circuit-breaker threshold values** (2026-08-04) — the shipped
  defaults (50% flagged fraction, 20-account minimum sample) are
  confirmed as the right numbers for this org's account volume; no
  code change needed. Notification routing when a run trips is still
  open (item 12 above, tied to item 9's admin-DL question).
- **SMTP relay connection details** (CloudOps, 2026-08-07) — host
  `postal.egcloud.no`:25, credential `egnorway/user-cleanup`, TLS off,
  From address `noreply@egcloud.no`. Live-verified via a real
  `send_warning_email()` smoke test received at mnost@eg.no. See item
  3 above for the rejected-From-address history.

