"""
Configuration for the deactivation-tracking pipeline. Backend is a CSV
dump with columns created, disabled, display_name, dn, domain,
domain_admin, email, last_logon, password_last_set, username.

SERVICE_ACCOUNT_MARKING_CONFIRMED is True: PMO has confirmed every
service account is also marked domain_admin (the reverse isn't true --
not every admin is a service account -- but since both groups are
excluded identically, domain_admin alone is a sufficient exclusion
signal for both).
"""

import os

from dotenv import load_dotenv

# Loads ad_deactivation_agent/.env (if present) into the environment
# before any os.environ.get() below runs -- lets SMTP relay credentials
# and other secrets live in a local, gitignored .env instead of being
# exported by hand or hardcoded. Does not override variables already
# set in the real environment (e.g. by Task Scheduler or CI).
load_dotenv()

# --- Field names in the raw CSV record, as given by the data owner ---
IDENTIFIER_FIELD = "username"
DISPLAY_NAME_FIELD = "display_name"
EMAIL_FIELD = "email"
LAST_LOGON_FIELD = "last_logon"
CREATED_FIELD = "created"
DOMAIN_FIELD = "domain"
DOMAIN_ADMIN_FIELD = "domain_admin"
DISABLED_FIELD = "disabled"

# --- Exclusion status ---
# domain_admin field handles both admins and service accounts: PMO has
# confirmed every service account is also flagged domain_admin, so this
# single field is a sufficient (if not precise) exclusion signal for both.
SERVICE_ACCOUNT_MARKING_CONFIRMED = True

# --- The previous and current CSV dumps are delivered together on every
# run; this agent never stores either one between runs (see CONTEXT.md
# "New Design: Snapshot Diff"). Both paths must be set as environment
# variables by whatever runs this script (Task Scheduler action, CI
# secret, etc) -- never hardcode.
PREVIOUS_USER_SOURCE_CSV_PATH = os.environ.get("PREVIOUS_USER_SOURCE_CSV_PATH")
CURRENT_USER_SOURCE_CSV_PATH = os.environ.get("CURRENT_USER_SOURCE_CSV_PATH")
NEW_DEACTIVATIONS_REPORT_CSV_PATH = os.environ.get(
    "NEW_DEACTIVATIONS_REPORT_CSV_PATH", "new_deactivations_report.csv"
)


def require_snapshot_paths() -> None:
    """Fail loudly and early if either snapshot path isn't set, rather
    than silently reading nothing."""
    if not PREVIOUS_USER_SOURCE_CSV_PATH or not CURRENT_USER_SOURCE_CSV_PATH:
        raise RuntimeError(
            "PREVIOUS_USER_SOURCE_CSV_PATH and CURRENT_USER_SOURCE_CSV_PATH "
            "must both be set as environment variables, pointing at the "
            "previous and current user data CSV dumps, before running "
            "this script."
        )


# --- Per-user inactivity classification (rebuilt 2026-07-23 to drive the
# revived warning/deactivation-notification emails, see CONTEXT.md and
# email_relay.py). Classifies against CURRENT_USER_SOURCE_CSV_PATH only
# -- it doesn't need the previous snapshot, since it's judging each
# account's current inactivity, not diffing two snapshots.
#
# Thresholds confirmed by PMO 2026-07-23 (days since last activity
# signal, sized for weekly job cadence) -- keep as-is. ---
WARNING_MIN_DAYS = 180
WARNING_MAX_DAYS = 186
DEACTIVATE_MIN_DAYS = 187


def require_current_snapshot_path() -> None:
    """Fail loudly and early if the current snapshot path isn't set."""
    if not CURRENT_USER_SOURCE_CSV_PATH:
        raise RuntimeError(
            "CURRENT_USER_SOURCE_CSV_PATH must be set as an environment "
            "variable pointing at the current user data CSV dump before "
            "running this script."
        )


# --- SMTP relay (end-user warning and deactivation-notification
# emails, see CONTEXT.md). email_relay.py is wired into
# email_notification_report.py, but email delivery remains on hold
# until explicitly turned on -- see WARNING_DELIVERY_ENABLED /
# DEACTIVATE_DELIVERY_ENABLED below. ---
SMTP_RELAY_HOST = os.environ.get("SMTP_RELAY_HOST")
SMTP_RELAY_PORT = int(os.environ.get("SMTP_RELAY_PORT", "587"))
SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "true").strip().lower() in ("true", "1", "yes")
SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
SMTP_FROM_ADDRESS = os.environ.get("SMTP_FROM_ADDRESS")


def require_smtp_config() -> None:
    """Fail loudly if the SMTP relay isn't configured, rather than
    silently failing (or worse, silently not sending) at send time."""
    if not SMTP_RELAY_HOST or not SMTP_FROM_ADDRESS:
        raise RuntimeError(
            "SMTP_RELAY_HOST and SMTP_FROM_ADDRESS must both be set as "
            "environment variables before any email can be sent."
        )


# --- Email delivery gates (see email_notification_report.py). Wiring
# classification to the relay is not the same thing as authorizing live
# sends -- email delivery is on hold as an explicit, independent
# decision. Split into two flags (PMO/IT, 2026-08-12) so warning emails
# can go live independently of deactivation-notice emails, per the
# confirmed staged rollout. Leave both False until each stage is
# explicitly approved; while a flag is False, the pipeline reports what
# it *would* send for that outcome instead of sending. ---
WARNING_DELIVERY_ENABLED = False
DEACTIVATE_DELIVERY_ENABLED = False

# --- Circuit breaker: guards both the diff pipeline
# (deactivation_report.py) and the email pipeline
# (email_notification_report.py) against an implausibly large fraction
# of accounts being flagged in a single run -- e.g. a bad CSV dump
# misclassifying most accounts as inactive. See circuit_breaker.py.
#
# CIRCUIT_BREAKER_MAX_FRACTION is the flagged/total fraction above which
# a run aborts instead of writing a report or sending mail.
# CIRCUIT_BREAKER_MIN_SAMPLE_SIZE keeps small runs (a handful of test/
# staging accounts) from tripping on fraction alone -- the breaker only
# makes sense once there's enough accounts for a fraction to be
# meaningful. Confirmed by PMO (2026-08-04) as the right numbers for
# this org's account volume; override via env var to tune without a
# code change. ---
CIRCUIT_BREAKER_MAX_FRACTION = float(os.environ.get("CIRCUIT_BREAKER_MAX_FRACTION", "0.5"))
CIRCUIT_BREAKER_MIN_SAMPLE_SIZE = int(os.environ.get("CIRCUIT_BREAKER_MIN_SAMPLE_SIZE", "20"))

# --- Admin run-summary recipients (confirmed by PMO 2026-08-07): a
# short summary of each email-notification run (counts:
# would-send/sent, missing-email, circuit-breaker-tripped) goes to these
# addresses after every run, regardless of either delivery flag -- the
# summary itself never contains per-user PII beyond counts, and is
# useful during the dry-run phase too. Comma-separated env var, falling
# back to the two addresses PMO named. ---
ADMIN_SUMMARY_RECIPIENTS = [
    addr.strip()
    for addr in os.environ.get("ADMIN_SUMMARY_RECIPIENTS", "onost@eg.no,nishh@eg.dk").split(",")
    if addr.strip()
]
