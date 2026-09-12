"""Notificaciones de la app (alertas de retraso de vuelos).

Módulo de envío de alertas por email. El servicio está desactivado por
defecto: solo envía cuando ``NOTIFICATIONS_ENABLED=true`` y hay configuración
SMTP válida (ver ``aeropredict.notifications.email``).
"""

from aeropredict.notifications.email import send_alert_email

__all__ = ["send_alert_email"]
