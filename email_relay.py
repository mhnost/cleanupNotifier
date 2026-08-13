"""
SMTP relay for the end-user email requirement (warning +
deactivation-notification emails, see CONTEXT.md).

Email delivery is on hold until explicitly turned on -- see
config.WARNING_DELIVERY_ENABLED / config.DEACTIVATE_DELIVERY_ENABLED.

Deliberately not addressed here: rate limiting / bounded concurrency,
bounce handling, and retries/backoff are all out of scope for v1
(confirmed by PO, 2026-08-13) -- "relay accepted the send" counts as
"notified".
"""

import smtplib
from datetime import datetime
from email.message import EmailMessage
from typing import Optional

import config

# Approved email copy (PO, 2026-08-13). WARNING_MIN_DAYS/
# DEACTIVATE_MIN_DAYS are confirmed (see config.py), so the day counts
# below are safe to state as fact.
WARNING_SUBJECT = "Your account will be deactivated soon"
DEACTIVATION_NOTICE_SUBJECT = "Your account has been deactivated"

# The domain name belongs in the email ("your account in the X domain").
# DOMAIN_DISPLAY_NAMES maps the raw AD/dashboard domain value to a
# friendlier org name, falling back to the raw value if unmapped,
# instead of showing the raw domain string to end users. Confirmed by
# PMO (2026-08-07) as the full, current set of relevant domains; the
# mapping and its fallback behavior are approved (PO, 2026-08-13).
DOMAIN_DISPLAY_NAMES = {
    "egrdrift": "EG Drift",
    "egrtest": "EG Test",
    "egrutv": "EG Utvikling",
    "ad-eg-no": "EG",
    "kesko": "Kesko",
    "mestergruppen": "Mestergruppen",
    "naestved-nlt": "Næstved NLT",
    "new-nordic-brandhouse": "New Nordic Brand House",
    "stangeskovene": "Stangeskovene",
    "trygg2000": "Trygg2000",
    "retailse": "Retail SWE",
}

# Used only when no raw domain value is available at all (e.g. a record
# with a blank domain column) -- distinct from the unmapped-domain case,
# which falls back to the raw value instead (see resolve_domain_display_name).
DOMAIN_PLACEHOLDER = "[DOMAIN]"


def resolve_domain_display_name(raw_domain: Optional[str]) -> str:
    """Map a raw AD domain to its display name.

    Falls back to the raw domain string if it isn't in the mapping, and
    to DOMAIN_PLACEHOLDER if there's no raw domain at all.
    """
    if not raw_domain:
        return DOMAIN_PLACEHOLDER
    return DOMAIN_DISPLAY_NAMES.get(raw_domain, raw_domain)


def _format_date(value: Optional[datetime]) -> str:
    if value is None:
        return "no logon on record"
    return value.strftime("%Y-%m-%d")


# Greeting uses display_name (PMO, 2026-08-07: more user-friendly and
# recognizable than the username) -- falls back to a name-less greeting
# if display_name is missing, rather than showing a username or a blank.
def _greeting(display_name: Optional[str]) -> str:
    if display_name:
        return f"Dear {display_name},"
    return "Hello,"


def build_warning_body(
    last_logon: Optional[datetime],
    deadline_date: Optional[datetime],
    domain: str = DOMAIN_PLACEHOLDER,
    display_name: Optional[str] = None,
) -> str:
    return (
        f"{_greeting(display_name)}\n\n"
        f"Your account in the {domain} domain has been inactive and will be "
        f"deactivated if you do not log in by {_format_date(deadline_date)}.\n"
        f"Last logon on record: {_format_date(last_logon)}.\n\n"
        "-- Automated notice"
    )


def build_deactivation_notice_body(
    last_logon: Optional[datetime],
    deadline_date: Optional[datetime],
    domain: str = DOMAIN_PLACEHOLDER,
    display_name: Optional[str] = None,
) -> str:
    return (
        f"{_greeting(display_name)}\n\n"
        f"Your account in the {domain} domain has been deactivated due to "
        f"prolonged inactivity. You had until {_format_date(deadline_date)} to log in.\n"
        f"Last logon on record: {_format_date(last_logon)}.\n\n"
        "-- Automated notice"
    )


def build_message(to_address: str, subject: str, body: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.SMTP_FROM_ADDRESS
    message["To"] = to_address
    message.set_content(body)
    return message


def send_email(to_address: str, subject: str, body: str) -> None:
    """Send a single email through the configured SMTP relay."""
    config.require_smtp_config()
    message = build_message(to_address, subject, body)
    with smtplib.SMTP(config.SMTP_RELAY_HOST, config.SMTP_RELAY_PORT, timeout=10) as server:
        if config.SMTP_USE_TLS:
            server.starttls()
        if config.SMTP_USERNAME and config.SMTP_PASSWORD:
            server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
        server.send_message(message)


def send_warning_email(
    to_address: str,
    last_logon: Optional[datetime] = None,
    deadline_date: Optional[datetime] = None,
    domain: str = DOMAIN_PLACEHOLDER,
    display_name: Optional[str] = None,
) -> None:
    send_email(
        to_address,
        WARNING_SUBJECT,
        build_warning_body(last_logon, deadline_date, domain, display_name),
    )


def send_deactivation_notice_email(
    to_address: str,
    last_logon: Optional[datetime] = None,
    deadline_date: Optional[datetime] = None,
    domain: str = DOMAIN_PLACEHOLDER,
    display_name: Optional[str] = None,
) -> None:
    send_email(
        to_address,
        DEACTIVATION_NOTICE_SUBJECT,
        build_deactivation_notice_body(last_logon, deadline_date, domain, display_name),
    )


# --- Admin run-summary (confirmed by PMO 2026-08-07) ---
SUMMARY_SUBJECT = "Deactivation-tracking email run summary"


def build_summary_body(
    mode: str,
    warned_count: int,
    notified_count: int,
    missing_email_count: int,
) -> str:
    """Counts only -- no usernames or other per-user PII, consistent
    with this project's GDPR-minimal logging stance (see CONTEXT.md)."""
    return (
        f"Deactivation-tracking email run summary ({mode})\n\n"
        f"Warned: {warned_count}\n"
        f"Deactivation-notice sent: {notified_count}\n"
        f"Missing email (skipped): {missing_email_count}\n\n"
        "-- Automated notice"
    )


def send_admin_notification(recipients: list, subject: str, body: str) -> None:
    """Send the same admin notification to every configured recipient.
    No-op if no recipients are configured."""
    for recipient in recipients:
        send_email(recipient, subject, body)


def send_summary_email(recipients: list, body: str) -> None:
    """Send the same run-summary body to every configured admin
    recipient. No-op if no recipients are configured."""
    send_admin_notification(recipients, SUMMARY_SUBJECT, body)


# --- Circuit-breaker trip notification (routed into the same admin
# recipients as the run summary, confirmed by PMO 2026-08-07) --
# previously stderr-only. ---
CIRCUIT_BREAKER_TRIP_SUBJECT = "Deactivation-tracking pipeline aborted (circuit breaker tripped)"


def send_circuit_breaker_trip_email(recipients: list, body: str) -> None:
    send_admin_notification(recipients, CIRCUIT_BREAKER_TRIP_SUBJECT, body)



