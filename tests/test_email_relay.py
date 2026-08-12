import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config  # noqa: E402
import email_relay  # noqa: E402


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.started_tls = False
        self.login_calls = []
        self.sent_messages = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.login_calls.append((username, password))

    def send_message(self, message):
        self.sent_messages.append(message)


def _configure_smtp(monkeypatch, use_tls=True, username=None, password=None):
    monkeypatch.setattr(config, "SMTP_RELAY_HOST", "relay.example.com")
    monkeypatch.setattr(config, "SMTP_RELAY_PORT", 587)
    monkeypatch.setattr(config, "SMTP_USE_TLS", use_tls)
    monkeypatch.setattr(config, "SMTP_USERNAME", username)
    monkeypatch.setattr(config, "SMTP_PASSWORD", password)
    monkeypatch.setattr(config, "SMTP_FROM_ADDRESS", "noreply@example.com")
    FakeSMTP.instances = []
    monkeypatch.setattr(email_relay.smtplib, "SMTP", FakeSMTP)


def test_require_smtp_config_raises_when_host_missing(monkeypatch):
    monkeypatch.setattr(config, "SMTP_RELAY_HOST", None)
    monkeypatch.setattr(config, "SMTP_FROM_ADDRESS", "noreply@example.com")
    try:
        config.require_smtp_config()
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_require_smtp_config_passes_when_configured(monkeypatch):
    monkeypatch.setattr(config, "SMTP_RELAY_HOST", "relay.example.com")
    monkeypatch.setattr(config, "SMTP_FROM_ADDRESS", "noreply@example.com")
    config.require_smtp_config()


def test_send_email_uses_starttls_and_sends_message(monkeypatch):
    _configure_smtp(monkeypatch, use_tls=True)

    email_relay.send_email("user@example.com", "Subject line", "Body text")

    server = FakeSMTP.instances[0]
    assert server.host == "relay.example.com"
    assert server.started_tls is True
    assert len(server.sent_messages) == 1
    sent = server.sent_messages[0]
    assert sent["To"] == "user@example.com"
    assert sent["Subject"] == "Subject line"
    assert sent["From"] == "noreply@example.com"
    assert sent.get_content().strip() == "Body text"


def test_send_email_skips_starttls_when_disabled(monkeypatch):
    _configure_smtp(monkeypatch, use_tls=False)

    email_relay.send_email("user@example.com", "Subject", "Body")

    assert FakeSMTP.instances[0].started_tls is False


def test_send_email_logs_in_when_credentials_configured(monkeypatch):
    _configure_smtp(monkeypatch, username="relay_user", password="relay_pass")

    email_relay.send_email("user@example.com", "Subject", "Body")

    assert FakeSMTP.instances[0].login_calls == [("relay_user", "relay_pass")]


def test_send_email_skips_login_when_no_credentials(monkeypatch):
    _configure_smtp(monkeypatch, username=None, password=None)

    email_relay.send_email("user@example.com", "Subject", "Body")

    assert FakeSMTP.instances[0].login_calls == []


def test_send_email_raises_when_smtp_not_configured(monkeypatch):
    monkeypatch.setattr(config, "SMTP_RELAY_HOST", None)
    monkeypatch.setattr(config, "SMTP_FROM_ADDRESS", None)
    try:
        email_relay.send_email("user@example.com", "Subject", "Body")
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_send_warning_email_uses_warning_template(monkeypatch):
    _configure_smtp(monkeypatch)
    last_logon = datetime(2026, 1, 1)
    deadline_date = datetime(2026, 7, 30)

    email_relay.send_warning_email("user@example.com", last_logon=last_logon, deadline_date=deadline_date)

    sent = FakeSMTP.instances[0].sent_messages[0]
    assert sent["Subject"] == email_relay.WARNING_SUBJECT
    body = sent.get_content()
    assert email_relay.DOMAIN_PLACEHOLDER in body
    assert "2026-01-01" in body
    assert "2026-07-30" in body


def test_send_deactivation_notice_email_uses_notice_template(monkeypatch):
    _configure_smtp(monkeypatch)
    last_logon = datetime(2026, 1, 1)
    deadline_date = datetime(2026, 7, 30)

    email_relay.send_deactivation_notice_email(
        "user@example.com", last_logon=last_logon, deadline_date=deadline_date
    )

    sent = FakeSMTP.instances[0].sent_messages[0]
    assert sent["Subject"] == email_relay.DEACTIVATION_NOTICE_SUBJECT
    body = sent.get_content()
    assert email_relay.DOMAIN_PLACEHOLDER in body
    assert "2026-01-01" in body
    assert "2026-07-30" in body


def test_warning_body_reports_no_logon_on_record_when_never_logged_in():
    body = email_relay.build_warning_body(last_logon=None, deadline_date=datetime(2026, 7, 30))
    assert "no logon on record" in body


def test_build_message_uses_custom_domain():
    body = email_relay.build_warning_body(
        last_logon=None, deadline_date=datetime(2026, 7, 30), domain="example.com"
    )
    assert "example.com" in body
    assert email_relay.DOMAIN_PLACEHOLDER not in body


def test_resolve_domain_display_name_maps_known_domains():
    assert email_relay.resolve_domain_display_name("ad-eg-no") == "EG"
    assert email_relay.resolve_domain_display_name("egrtest") == "EG Test"
    assert email_relay.resolve_domain_display_name("egrutv") == "EG Utvikling"
    assert email_relay.resolve_domain_display_name("egrdrift") == "EG Drift"
    assert email_relay.resolve_domain_display_name("kesko") == "Kesko"
    assert email_relay.resolve_domain_display_name("mestergruppen") == "Mestergruppen"
    assert email_relay.resolve_domain_display_name("naestved-nlt") == "Næstved NLT"
    assert email_relay.resolve_domain_display_name("new-nordic-brandhouse") == "New Nordic Brand House"
    assert email_relay.resolve_domain_display_name("stangeskovene") == "Stangeskovene"
    assert email_relay.resolve_domain_display_name("trygg2000") == "Trygg2000"
    assert email_relay.resolve_domain_display_name("retailse") == "Retail SWE"


def test_resolve_domain_display_name_falls_back_to_raw_value_when_unmapped():
    assert email_relay.resolve_domain_display_name("unknown.example.com") == "unknown.example.com"


def test_resolve_domain_display_name_falls_back_to_placeholder_when_missing():
    assert email_relay.resolve_domain_display_name(None) == email_relay.DOMAIN_PLACEHOLDER
    assert email_relay.resolve_domain_display_name("") == email_relay.DOMAIN_PLACEHOLDER


def test_warning_body_greets_by_display_name_when_present():
    body = email_relay.build_warning_body(
        last_logon=None, deadline_date=datetime(2026, 7, 30), display_name="Alice Andersen"
    )
    assert body.startswith("Hello Alice Andersen,")


def test_warning_body_greets_generically_when_display_name_missing():
    body = email_relay.build_warning_body(last_logon=None, deadline_date=datetime(2026, 7, 30))
    assert body.startswith("Hello,")


def test_send_warning_email_passes_display_name_through(monkeypatch):
    _configure_smtp(monkeypatch)

    email_relay.send_warning_email(
        "user@example.com",
        last_logon=None,
        deadline_date=datetime(2026, 7, 30),
        display_name="Bob Berg",
    )

    body = FakeSMTP.instances[0].sent_messages[0].get_content()
    assert body.startswith("Hello Bob Berg,")


def test_build_summary_body_reports_counts_only():
    body = email_relay.build_summary_body(
        mode="DRY RUN", warned_count=3, notified_count=1, missing_email_count=2
    )
    assert "Warned: 3" in body
    assert "Deactivation-notice sent: 1" in body
    assert "Missing email (skipped): 2" in body


def test_send_summary_email_sends_to_every_recipient(monkeypatch):
    _configure_smtp(monkeypatch)

    email_relay.send_summary_email(["onost@eg.no", "nishh@eg.dk"], "Summary body")

    sent = [instance.sent_messages[0] for instance in FakeSMTP.instances]
    assert [m["To"] for m in sent] == ["onost@eg.no", "nishh@eg.dk"]
    assert [m["Subject"] for m in sent] == [email_relay.SUMMARY_SUBJECT, email_relay.SUMMARY_SUBJECT]


def test_send_summary_email_is_noop_with_no_recipients(monkeypatch):
    _configure_smtp(monkeypatch)

    email_relay.send_summary_email([], "Summary body")

    assert FakeSMTP.instances == []


def test_send_circuit_breaker_trip_email_sends_to_every_recipient(monkeypatch):
    _configure_smtp(monkeypatch)

    email_relay.send_circuit_breaker_trip_email(["onost@eg.no", "nishh@eg.dk"], "Trip body")

    sent = [instance.sent_messages[0] for instance in FakeSMTP.instances]
    assert [m["To"] for m in sent] == ["onost@eg.no", "nishh@eg.dk"]
    assert [m["Subject"] for m in sent] == [
        email_relay.CIRCUIT_BREAKER_TRIP_SUBJECT,
        email_relay.CIRCUIT_BREAKER_TRIP_SUBJECT,
    ]
    assert [m.get_content().strip() for m in sent] == ["Trip body", "Trip body"]
