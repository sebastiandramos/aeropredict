"""Capa de persistencia de la app — usuarios, suscripciones de vuelos y alertas.

Colecciones MongoDB:
    ``users``
        Usuarios registrados (login email/password + JWT). 1 doc por usuario,
        con ``user_id`` (uuid4 hex) como identificador estable y ``email`` único.

    ``user_flight_subscriptions``
        Vuelos seguidos por usuario. 1 doc por (user_id, flight_key), con
        índice único compuesto para que re-seguir un vuelo actualice en vez de
        duplicar.

Tabla PostgreSQL:
    ``gold.alerts``
        Alertas de retraso generadas para un usuario y vuelo. La severidad la
        decide el llamador (este módulo es persistencia pura, no calcula
        severidad).

Este módulo NO implementa auth, tokens, API ni scheduler — solo persistencia.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import psycopg2
import pymongo
from pymongo.collection import Collection

from ..opensky.config import get_mongo_uri, get_postgres_uri

logger = logging.getLogger(__name__)

# Conexión perezosa a MongoDB (se conecta en el primer uso)
_client: pymongo.MongoClient[Any] | None = None
_users_indexes_ensure = False
_subscriptions_indexes_ensure = False

# Conexión perezosa a PostgreSQL
_conn: Any = None

ALERTS_SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS gold;

CREATE TABLE IF NOT EXISTS gold.alerts (
    id                      SERIAL PRIMARY KEY,
    user_id                 TEXT NOT NULL,
    flight_key              TEXT NOT NULL,
    severity                TEXT NOT NULL,
    delay_minutes_predicted FLOAT,
    factor_jsonb            JSONB,
    email_sent              BOOLEAN NOT NULL DEFAULT FALSE,
    read                    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alerts_user_id ON gold.alerts (user_id);
"""


# ===================================================================
# MongoDB — conexión
# ===================================================================


def _connect() -> None:
    """Conecta a MongoDB si no hay conexión activa."""
    global _client
    if _client is None:
        uri = get_mongo_uri()
        logger.debug("Conectando a MongoDB: %s", uri)
        _client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=5000)
        _client.admin.command("ping")


def _get_db() -> pymongo.database.Database[Any]:
    """Devuelve la base de datos, asegurando conexión e índices."""
    _connect()
    assert _client is not None
    return _client.get_database()


def _get_users_collection() -> Collection[Any]:
    """Devuelve colección ``users`` con índice único por email."""
    global _users_indexes_ensure
    db = _get_db()
    if not _users_indexes_ensure:
        col = db["users"]
        col.create_index("email", unique=True)
        _users_indexes_ensure = True
    return db["users"]


def _get_subscriptions_collection() -> Collection[Any]:
    """Devuelve colección ``user_flight_subscriptions`` con índice único."""
    global _subscriptions_indexes_ensure
    db = _get_db()
    if not _subscriptions_indexes_ensure:
        col = db["user_flight_subscriptions"]
        col.create_index(
            [("user_id", pymongo.ASCENDING), ("flight_key", pymongo.ASCENDING)],
            unique=True,
        )
        _subscriptions_indexes_ensure = True
    return db["user_flight_subscriptions"]


def close() -> None:
    """Cierra la conexión a MongoDB."""
    global _client
    if _client is not None:
        _client.close()
        _client = None


# ===================================================================
# MongoDB — usuarios
# ===================================================================


def create_user(
    email: str,
    password_hash: str,
    auth_method: str = "email",
) -> dict[str, Any] | None:
    """Crea un usuario en la colección ``users``.

    Args:
        email: Email del usuario (único).
        password_hash: Hash de la contraseña (el llamador lo genera).
        auth_method: Método de autenticación (por defecto ``email``).

    Returns:
        El documento del usuario creado, o ``None`` si el email ya existe.
    """
    col = _get_users_collection()
    user_id = uuid4().hex
    now = datetime.now(UTC)
    doc = {
        "user_id": user_id,
        "email": email,
        "password_hash": password_hash,
        "auth_method": auth_method,
        "created_at": now,
    }
    try:
        col.insert_one(doc)
    except pymongo.errors.DuplicateKeyError:
        logger.warning("Email ya registrado: %s", email)
        return None
    return doc


def get_user_by_email(email: str) -> dict[str, Any] | None:
    """Devuelve el usuario con el email dado, o ``None`` si no existe."""
    col = _get_users_collection()
    return col.find_one({"email": email})


def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    """Devuelve el usuario con el ``user_id`` dado, o ``None`` si no existe."""
    col = _get_users_collection()
    return col.find_one({"user_id": user_id})


def update_user(user_id: str, **fields: Any) -> dict[str, Any] | None:
    """Actualiza campos de un usuario y devuelve el documento actualizado.

    Args:
        user_id: Identificador del usuario.
        **fields: Campos a actualizar (p. ej. ``password_hash``).

    Returns:
        El documento actualizado, o ``None`` si el usuario no existe.
    """
    col = _get_users_collection()
    if not fields:
        return get_user_by_id(user_id)
    col.update_one({"user_id": user_id}, {"$set": fields})
    return get_user_by_id(user_id)


# ===================================================================
# MongoDB — suscripciones de vuelos
# ===================================================================


def create_subscription(
    user_id: str,
    flight_key: str,
    flight_number: str,
    from_airport: str,
    to_airport: str,
    schedule_local: str,
    threshold_minutes: int = 60,
    email: str | None = None,
) -> dict[str, Any]:
    """Crea o actualiza (upsert) una suscripción de vuelo para un usuario.

    ``flight_key`` es un identificador estable aportado por el usuario; si ya
    existe una suscripción con el mismo (user_id, flight_key), se actualiza en
    lugar de duplicar.

    Args:
        user_id: Identificador del usuario.
        flight_key: Identificador estable del vuelo (aportado por el usuario).
        flight_number: Número de vuelo (p. ej. ``IB1234``).
        from_airport: Aeropuerto de origen.
        to_airport: Aeropuerto de destino.
        schedule_local: Horario programado en hora local.
        threshold_minutes: Umbral de retraso en minutos para alertar.
        email: Email de notificación (opcional).

    Returns:
        El documento de la suscripción (creado o actualizado).
    """
    col = _get_subscriptions_collection()
    now = datetime.now(UTC)
    doc = {
        "user_id": user_id,
        "flight_key": flight_key,
        "flight_number": flight_number,
        "from_airport": from_airport,
        "to_airport": to_airport,
        "schedule_local": schedule_local,
        "threshold_minutes": threshold_minutes,
        "email": email,
        "updated_at": now,
    }
    col.update_one(
        {"user_id": user_id, "flight_key": flight_key},
        {"$set": doc, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return col.find_one({"user_id": user_id, "flight_key": flight_key}) or doc


def list_subscriptions(user_id: str) -> list[dict[str, Any]]:
    """Devuelve las suscripciones de vuelo de un usuario."""
    col = _get_subscriptions_collection()
    return list(col.find({"user_id": user_id}))


def get_subscription(user_id: str, flight_key: str) -> dict[str, Any] | None:
    """Devuelve una suscripción concreta, o ``None`` si no existe."""
    col = _get_subscriptions_collection()
    return col.find_one({"user_id": user_id, "flight_key": flight_key})


def delete_subscription(user_id: str, flight_key: str) -> bool:
    """Elimina una suscripción de vuelo.

    Returns:
        ``True`` si se eliminó, ``False`` si no existía.
    """
    col = _get_subscriptions_collection()
    result = col.delete_one({"user_id": user_id, "flight_key": flight_key})
    return result.deleted_count > 0


# ===================================================================
# PostgreSQL — alertas
# ===================================================================


def _get_conn() -> Any:
    """Conecta a PostgreSQL y crea la tabla ``gold.alerts`` si no existe."""
    global _conn
    if _conn is None or _conn.closed:
        uri = get_postgres_uri()
        logger.debug("Conectando a PostgreSQL: %s", uri)
        _conn = psycopg2.connect(uri)
        _conn.autocommit = True
        with _conn.cursor() as cur:
            cur.execute(ALERTS_SCHEMA_SQL)
    return _conn


def close_pg() -> None:
    """Cierra la conexión a PostgreSQL."""
    global _conn
    if _conn is not None and not _conn.closed:
        _conn.close()
    _conn = None


def insert_alert(
    user_id: str,
    flight_key: str,
    severity: str,
    delay_minutes_predicted: float,
    factor_jsonb: dict[str, Any],
    email_sent: bool = False,
) -> int:
    """Inserta una alerta de retraso en ``gold.alerts``.

    Args:
        user_id: Identificador del usuario.
        flight_key: Identificador estable del vuelo.
        severity: Severidad de la alerta (la decide el llamador).
        delay_minutes_predicted: Minutos de retraso predichos.
        factor_jsonb: Factores explicativos como JSONB.
        email_sent: Si ya se envió el email de notificación.

    Returns:
        El id (SERIAL) de la alerta insertada.
    """
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gold.alerts
                (user_id, flight_key, severity, delay_minutes_predicted,
                 factor_jsonb, email_sent)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s)
            RETURNING id
            """,
            (
                user_id,
                flight_key,
                severity,
                delay_minutes_predicted,
                factor_jsonb,
                email_sent,
            ),
        )
        row = cur.fetchone()
    conn.commit()
    assert row is not None
    return int(row[0])


def list_alerts(user_id: str, read: bool | None = None) -> list[dict[str, Any]]:
    """Devuelve las alertas de un usuario, opcionalmente filtradas por leídas.

    Args:
        user_id: Identificador del usuario.
        read: Si ``True`` solo alertas leídas; si ``False`` solo no leídas;
            si ``None`` todas.

    Returns:
        Lista de dicts con las columnas de ``gold.alerts``.
    """
    conn = _get_conn()
    query = "SELECT * FROM gold.alerts WHERE user_id = %s"
    params: list[Any] = [user_id]
    if read is not None:
        query += " AND read = %s"
        params.append(read)
    query += " ORDER BY created_at DESC"
    with conn.cursor() as cur:
        cur.execute(query, params)
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def mark_alert_read(alert_id: int) -> bool:
    """Marca una alerta como leída.

    Args:
        alert_id: Id de la alerta.

    Returns:
        ``True`` si se actualizó, ``False`` si la alerta no existe.
    """
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE gold.alerts SET read = TRUE WHERE id = %s",
            (alert_id,),
        )
        updated = cur.rowcount
    conn.commit()
    return updated > 0
