"""Envío de alertas por email (SMTP).

El servicio está **desactivado por defecto**: ``send_alert_email`` solo
intenta enviar cuando se cumplen TODAS las condiciones:

1. El destinatario es un email válido y no vacío.
2. ``NOTIFICATIONS_ENABLED=true`` en el entorno (default OFF).
3. Hay configuración SMTP (al menos ``SMTP_HOST``).

La ruta de envío está completamente implementada (MIME RFC 5322 +
``smtplib``) y funciona cuando se activa; en cualquier otro caso se registra
un log y se devuelve ``False`` sin intentar conectar.
"""

from __future__ import annotations

import logging
import os
import re
import smtplib
from email.message import EmailMessage
from email.utils import formatdate

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

DEFAULT_SMTP_PORT = 587
DEFAULT_FROM = "noreply@aeropredict.app"

# Regex simple de email (RFC 5322 aproximado) para validar el destinatario.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------


def get_smtp_config() -> dict[str, str] | None:
    """Lee la configuración SMTP desde variables de entorno.

    Returns:
        Dict con ``host``, ``port``, ``user``, ``password`` y ``from_addr``,
        o ``None`` si falta ``SMTP_HOST`` (no se puede enviar).
    """
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        return None

    user = os.environ.get("SMTP_USER", "").strip()
    from_addr = os.environ.get("SMTP_FROM", "").strip() or user or DEFAULT_FROM

    return {
        "host": host,
        "port": os.environ.get("SMTP_PORT", str(DEFAULT_SMTP_PORT)).strip(),
        "user": user,
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "from_addr": from_addr,
    }


def _is_valid_email(address: str) -> bool:
    """Valida un email de forma simple (no vacío y con formato básico)."""
    return bool(_EMAIL_RE.match(address.strip()))


def _use_starttls(port: int) -> bool:
    """Decide si usar STARTTLS: puerto 587 o ``SMTP_STARTTLS != "false"``."""
    if os.environ.get("SMTP_STARTTLS", "").strip().lower() == "false":
        return False
    return port == 587 or port == 25


# ---------------------------------------------------------------------------
# Envío
# ---------------------------------------------------------------------------


def send_alert_email(to: str, subject: str, body: str) -> bool:
    """Envía una alerta de retraso por email.

    Devuelve ``True`` solo si el email se envió correctamente. En cualquier
    otro caso (destinatario inválido, notificaciones desactivadas, SMTP sin
    configurar o error de envío) registra un log y devuelve ``False``.

    Args:
        to: Email del destinatario.
        subject: Asunto del mensaje.
        body: Cuerpo del mensaje (texto plano).

    Returns:
        ``True`` si se envió, ``False`` en caso contrario.
    """
    # 1. Destinatario válido y no vacío.
    if not to or not _is_valid_email(to):
        logger.warning("Destinatario de alerta inválido o vacío: %r", to)
        return False

    # 2. Notificaciones desactivadas por defecto.
    if os.environ.get("NOTIFICATIONS_ENABLED", "").strip().lower() != "true":
        logger.info("Notificaciones desactivadas (NOTIFICATIONS_ENABLED != true); no se envía")
        return False

    # 3. Configuración SMTP.
    config = get_smtp_config()
    if config is None:
        logger.warning("SMTP_HOST no configurado; no se puede enviar la alerta")
        return False

    # 4. Construir y enviar el mensaje.
    try:
        port = int(config["port"])
    except ValueError:
        logger.error("SMTP_PORT inválido: %r", config["port"])
        return False

    msg = EmailMessage()
    msg["From"] = config["from_addr"]
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(body)

    try:
        with smtplib.SMTP(config["host"], port, timeout=30) as server:
            if _use_starttls(port):
                server.starttls()
            if config["user"]:
                server.login(config["user"], config["password"])
            server.send_message(msg)
        logger.info("Alerta enviada a %s (asunto: %r)", to, subject)
        return True
    except Exception as exc:  # cualquier fallo SMTP → False
        logger.error("Error al enviar alerta a %s: %s", to, exc)
        return False
