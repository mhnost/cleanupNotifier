# Phased Rollout Plan — Deactivation-Tracking Email Pipeline

Rollout order for taking `email_notification_report.py` from dry-run to
fully live. This was `TODO.md` item 7. **Confirmed by PMO/IT
(2026-08-12).** Drafted 2026-08-07 as a concrete proposal to bring to
PMO/IT; the team confirmed the phase order as-is, including entering
Phase 2 as warnings-only before moving to full scope. See `TODO.md` for
the current status and `CONTEXT.md` for the design rationale behind
the pipeline itself.

**Kill switches:** `config.WARNING_DELIVERY_ENABLED` and
`config.DEACTIVATE_DELIVERY_ENABLED` (split 2026-08-12, per the "Open
implementation note" this doc originally called for) gate warning and
deactivation-notice sends independently. Setting either back to
`False` at any point immediately reverts that outcome to dry-run — no
other code change needed to pause.

## Quick reference: flag state per phase

| Phase | `WARNING_DELIVERY_ENABLED` | `DEACTIVATE_DELIVERY_ENABLED` |
|---|---|---|
| 0 — Pre-flight | `False` | `False` |
| 1 — Dry run | `False` | `False` |
| 2 — Live warnings only | **`True`** | `False` |
| 3 — Fully live | `True` | **`True`** |

**Both flags are plain Python booleans hardcoded in `config.py`**
(`WARNING_DELIVERY_ENABLED = False` / `DEACTIVATE_DELIVERY_ENABLED = False`,
around line 122) — unlike the SMTP settings above them in that file,
they are **not** read from an environment variable. To move between
phases:
1. Edit the flag(s) in `config.py` to match the target phase's row
   above.
2. Commit that change (it's a deliberate, auditable code change, not a
   runtime toggle — this is intentional, so who-changed-what-when
   shows up in version history rather than needing to be inferred from
   deploy/ops logs).
3. Deploy/redeploy so the next scheduled run picks up the new value.

**To check which phase is currently live**, read those two lines in
`config.py` directly (or run
`python -c "import config; print(config.WARNING_DELIVERY_ENABLED, config.DEACTIVATE_DELIVERY_ENABLED)"`)
and match against the table above — there is no separate "current
phase" setting to go stale or drift out of sync with the flags.

**To roll back a phase** (e.g. Phase 2 warning complaints spike): flip
the relevant flag back to `False` and redeploy. This is exactly the
same mechanism as advancing — there's no separate rollback procedure,
and no per-user state to clean up (see CONTEXT.md's GDPR-minimal
logging constraints — nothing was stored that needs reverting).

## Phase 0 — Pre-flight (before Phase 1 starts)
- Confirm `config.CIRCUIT_BREAKER_MAX_FRACTION`/`MIN_SAMPLE_SIZE`
  still make sense against the real current account population
  (already confirmed 2026-08-04, just re-check the numbers haven't
  gone stale).
- Confirm admin summary + circuit-breaker-trip emails are landing
  correctly at onost@eg.no / nishh@eg.dk (already smoke-tested,
  see `TODO.md` item 9/12).
- **Owner sign-off to enter Phase 1:** PMO.

## Phase 1 — Dry run, N weeks (proposed N = 3)
- Both flags stay `False` (unchanged from today) — pipeline runs
  weekly, classifies every account, logs `would_send` counts for both
  outcomes, sends the real admin summary email each run.
- **What we're checking:** the *volume and shape* of who gets
  flagged — does the warned/notified/missing-email count look
  plausible each week, does it stay roughly stable (not swinging
  wildly), does the missing-email list look like real data gaps
  rather than a mapping bug.
- **Exit criteria:** 3 consecutive weekly summaries reviewed by PMO
  with no unexplained spikes, no circuit-breaker trips, missing-email
  list small/explainable.
- **Owner sign-off to enter Phase 2:** PMO + IT.

## Phase 2 — Live warnings only (proposed duration: 2–4 weeks)
- Flip `config.WARNING_DELIVERY_ENABLED = True`; leave
  `config.DEACTIVATE_DELIVERY_ENABLED = False` — only the `WARNING`
  branch of `dispatch()` actually sends, deactivation-notice sends
  stay dry-run. The flag split is built (`config.py`,
  `email_notification_report.py`).
- **What we're checking:** real end users receiving real warning
  emails — any confusion, complaints, or helpdesk tickets from
  recipients; does the copy read as expected in an actual inbox (not
  just the smoke test already sent to mnost@eg.no).
- **Exit criteria:** N warning cycles with no material complaint
  volume, no relay-side rejection/throttling problems at real volume.
- **Owner sign-off to enter Phase 3:** PMO + IT.

## Phase 3 — Live deactivation-notices
- Flip `config.DEACTIVATE_DELIVERY_ENABLED = True`. Full pipeline live.
- **What we're checking:** deactivation-notice emails actually arrive
  close enough to the real deactivation event — this is where the
  confirmed day-187 timing alignment (`TODO.md` item 8) gets tested
  against reality for the first time.
- **Ongoing owner:** PMO + IT jointly own the go/no-go to roll back to
  a prior phase if problems emerge post-launch.

