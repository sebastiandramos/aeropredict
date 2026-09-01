"""Tests del módulo de notificaciones por email (SMTP).

Verifica que la ruta de envío está **completamente implementada y funcional**
cuando se activa (``NOTIFICATIONS_ENABLED=true`` + SMTP configurado), y que
permanece inactiva por defecto. Todo el tráfico SMTP se mockea con una clase
fake — sin red real.
"""

import smtplib
from typing import ClassVar

from aeropredict.notifications import email as email_module


class FakeSMTP:
    """Fake de ``smtplib.SMTP`` que registra las llamadas y captura el mensaje."""

    instances: ClassVar[list["FakeSMTP"]] = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.starttls_called = False
        self.login_called = False
        self.login_args = None
        self.sent_messages = []
        self.quit_called = False
        FakeSMTP.instances.append(self)

    def starttls(self):
        self.starttls_called = True

    def login(self, user, password):
        self.login_called = True
        self.login_args = (user, password)

    def send_message(self, msg):
        self.sent_messages.append(msg)

    def quit(self):
        self.quit_called = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)


def _enable_notifications(monkeypatch):
    monkeypatch.setenv("NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "alerts@aeropredict.app")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("SMTP_FROM", "alerts@aeropredict.app")


def test_send_alert_email_sends_when_enabled(monkeypatch):
    """Given notificaciones activadas y SMTP configurado, When se envía,
    Then devuelve True y el SMTP fake recibió el mensaje correcto."""
    _patch_smtp(monkeypatch)
    _enable_notifications(monkeypatch)

    result = email_module.send_alert_email("a@b.com", "Retraso", "Tu vuelo va tarde")

    assert result is True
    assert len(FakeSMTP.instances) == 1
    server = FakeSMTP.instances[0]
    assert server.host == "smtp.example.com"
    assert server.port == 587
    assert server.starttls_called is True
    assert server.login_called is True
    assert server.login_args == ("alerts@aeropredict.app", "secret")
    assert len(server.sent_messages) == 1
    msg = server.sent_messages[0]
    assert msg["From"] == "alerts@aeropredict.app"
    assert msg["To"] == "a@b.com"
    assert msg["Subject"] == "Retraso"
    assert msg.get_content().strip() == "Tu vuelo va tarde"


def test_send_alert_email_disabled_by_default(monkeypatch):
    """Given NOTIFICATIONS_ENABLED no está a true, When se llama,
    Then devuelve False y SMTP nunca conecta."""
    _patch_smtp(monkeypatch)
    monkeypatch.delenv("NOTIFICATIONS_ENABLED", raising=False)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")

    result = email_module.send_alert_email("a@b.com", "s", "body")

    assert result is False
    assert FakeSMTP.instances == []


def test_send_alert_email_invalid_recipient(monkeypatch):
    """Given destinatario vacío o inválido, When se llama,
    Then devuelve False y SMTP nunca conecta."""
    _patch_smtp(monkeypatch)
    _enable_notifications(monkeypatch)

    assert email_module.send_alert_email("", "s", "body") is False
    assert email_module.send_alert_email(None, "s", "body") is False
    assert email_module.send_alert_email("not-an-email", "s", "body") is False
    assert FakeSMTP.instances == []


def test_send_alert_email_missing_smtp_host(monkeypatch):
    """Given SMTP_HOST no configurado, When se llama,
    Then devuelve False y SMTP nunca conecta."""
    _patch_smtp(monkeypatch)
    _enable_notifications(monkeypatch)
    monkeypatch.delenv("SMTP_HOST", raising=False)

    result = email_module.send_alert_email("a@b.com", "s", "body")

    assert result is False
    assert FakeSMTP.instances == []


def test_send_alert_email_returns_false_on_smtp_error(monkeypatch):
    """Given el SMTP lanza una excepción, When se envía,
    Then devuelve False (no propaga el error)."""
    _patch_smtp(monkeypatch)
    _enable_notifications(monkeypatch)

    class FailingSMTP(FakeSMTP):
        def send_message(self, msg):
            raise smtplib.SMTPException("connection refused")

    monkeypatch.setattr(smtplib, "SMTP", FailingSMTP)

    result = email_module.send_alert_email("a@b.com", "s", "body")

    assert result is False
