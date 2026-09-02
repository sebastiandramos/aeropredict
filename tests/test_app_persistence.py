"""Tests de la capa de persistencia de la app (todo 2, mis-vuelos-alertas).

Patrón importlib + monkeypatch: sin red, sin DB real, sin Doppler. Se
monkeypatchea ``aeropredict.app.persistence._client`` y ``_get_db`` con un
fake de colección en memoria, y ``_get_conn`` con una conexión psycopg2 fake.
"""

from __future__ import annotations

from typing import Any

import pymongo
import pytest

from aeropredict.app import persistence

# ===================================================================
# Fakes en memoria
# ===================================================================


class _FakeResult:
    """Resultado de delete_one/update_one con contadores."""

    def __init__(
        self,
        deleted_count: int = 0,
        modified_count: int = 0,
        upserted_id: Any = None,
    ) -> None:
        self.deleted_count = deleted_count
        self.modified_count = modified_count
        self.upserted_id = upserted_id


class _FakeCursor:
    """Cursor que itera sobre una lista de documentos."""

    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def __iter__(self):
        return iter(self._docs)


class _FakeCollection:
    """Colección MongoDB en memoria con soporte de índice único."""

    def __init__(self) -> None:
        self._docs: list[dict[str, Any]] = []
        self._unique_indexes: list[list[str]] = []

    def create_index(self, keys: Any, unique: bool = False) -> None:
        if unique:
            if isinstance(keys, list):
                self._unique_indexes.append([k for k, _ in keys])
            else:
                self._unique_indexes.append([keys])

    def _matches(self, doc: dict[str, Any], filtro: dict[str, Any]) -> bool:
        return all(doc.get(k) == v for k, v in filtro.items())

    def _check_unique(self, doc: dict[str, Any]) -> None:
        for idx in self._unique_indexes:
            for existing in self._docs:
                if all(existing.get(k) == doc.get(k) for k in idx):
                    raise pymongo.errors.DuplicateKeyError(
                        f"duplicate key on {idx}"
                    )

    def insert_one(self, doc: dict[str, Any]) -> Any:
        self._check_unique(doc)
        self._docs.append(dict(doc))
        return _FakeResult()

    def find_one(self, filtro: dict[str, Any]) -> dict[str, Any] | None:
        for doc in self._docs:
            if self._matches(doc, filtro):
                return dict(doc)
        return None

    def find(self, filtro: dict[str, Any]) -> _FakeCursor:
        return _FakeCursor([dict(d) for d in self._docs if self._matches(d, filtro)])

    def update_one(
        self,
        filtro: dict[str, Any],
        update: dict[str, Any],
        upsert: bool = False,
    ) -> _FakeResult:
        for _i, doc in enumerate(self._docs):
            if self._matches(doc, filtro):
                if "$set" in update:
                    doc.update(update["$set"])
                if "$setOnInsert" in update:
                    for k, v in update["$setOnInsert"].items():
                        doc.setdefault(k, v)
                return _FakeResult(modified_count=1)
        if upsert:
            new_doc = dict(filtro)
            if "$set" in update:
                new_doc.update(update["$set"])
            if "$setOnInsert" in update:
                new_doc.update(update["$setOnInsert"])
            self._check_unique(new_doc)
            self._docs.append(new_doc)
            return _FakeResult(modified_count=1, upserted_id="new")
        return _FakeResult()

    def delete_one(self, filtro: dict[str, Any]) -> _FakeResult:
        for i, doc in enumerate(self._docs):
            if self._matches(doc, filtro):
                self._docs.pop(i)
                return _FakeResult(deleted_count=1)
        return _FakeResult()


class _FakeDatabase:
    """Base de datos en memoria con colecciones."""

    def __init__(self) -> None:
        self._collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        if name not in self._collections:
            self._collections[name] = _FakeCollection()
        return self._collections[name]


class _FakeCursorPG:
    """Cursor psycopg2 fake que registra ejecuciones y devuelve filas."""

    def __init__(self) -> None:
        self._rows: list[tuple[Any, ...]] = []
        self._index = 0
        self.description: list[tuple[str, ...]] | None = None
        self.rowcount = 0
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        self.executed.append((query, params or ()))
        if query.strip().upper().startswith("CREATE"):
            self.description = None
            self.rowcount = 0
            return
        if "RETURNING id" in query:
            self._rows = [(1,)]
            self.rowcount = 1
            self.description = [("id",)]
            return
        if query.strip().upper().startswith("UPDATE"):
            self.rowcount = 1
            self.description = None
            return
        if query.strip().upper().startswith("SELECT"):
            # list_alerts: filtra por read si el query lo pide
            read_filter = None
            if "read = %s" in query and params:
                read_filter = params[-1]
            if read_filter is True:
                self._rows = []
            else:
                self._rows = [
                    (1, "u1", "fk1", "alta", 45.0, {"weather": True}, False, False, "2026-09-01"),
                ]
            self.description = [
                ("id",), ("user_id",), ("flight_key",), ("severity",),
                ("delay_minutes_predicted",), ("factor_jsonb",), ("email_sent",),
                ("read",), ("created_at",),
            ]
            self.rowcount = len(self._rows)
            return
        self.rowcount = 0

    def fetchone(self) -> tuple[Any, ...] | None:
        if self._index < len(self._rows):
            row = self._rows[self._index]
            self._index += 1
            return row
        return None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def __enter__(self) -> _FakeCursorPG:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class _FakeConnectionPG:
    """Conexión psycopg2 fake."""

    def __init__(self) -> None:
        self.autocommit = True
        self.closed = False
        self._cursor: _FakeCursorPG | None = None

    def cursor(self) -> _FakeCursorPG:
        self._cursor = _FakeCursorPG()
        return self._cursor

    def commit(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_mongo(monkeypatch):
    """Monkeypatchea la conexión Mongo con una base en memoria."""
    db = _FakeDatabase()
    monkeypatch.setattr(persistence, "_client", object())
    monkeypatch.setattr(persistence, "_get_db", lambda: db)
    # Reinicia los guards de índices para que cada test cree sus índices
    monkeypatch.setattr(persistence, "_users_indexes_ensure", False)
    monkeypatch.setattr(persistence, "_subscriptions_indexes_ensure", False)
    return db


@pytest.fixture
def fake_pg(monkeypatch):
    """Monkeypatchea la conexión PostgreSQL con una conexión fake."""
    conn = _FakeConnectionPG()
    monkeypatch.setattr(persistence, "_get_conn", lambda: conn)
    return conn


# ===================================================================
# Usuarios
# ===================================================================


def test_create_user_and_get_by_email(fake_mongo):
    user = persistence.create_user("ana@example.com", "hash1")

    assert user is not None
    assert user["email"] == "ana@example.com"
    assert user["password_hash"] == "hash1"
    assert user["auth_method"] == "email"
    assert user["user_id"]

    fetched = persistence.get_user_by_email("ana@example.com")
    assert fetched is not None
    assert fetched["user_id"] == user["user_id"]


def test_create_user_duplicate_email_returns_none(fake_mongo):
    persistence.create_user("ana@example.com", "hash1")

    dup = persistence.create_user("ana@example.com", "hash2")

    assert dup is None
    # El usuario original sigue intacto
    fetched = persistence.get_user_by_email("ana@example.com")
    assert fetched is not None
    assert fetched["password_hash"] == "hash1"


def test_get_user_by_id_and_update(fake_mongo):
    user = persistence.create_user("ana@example.com", "hash1")

    by_id = persistence.get_user_by_id(user["user_id"])
    assert by_id is not None
    assert by_id["email"] == "ana@example.com"

    updated = persistence.update_user(user["user_id"], password_hash="hash2")
    assert updated is not None
    assert updated["password_hash"] == "hash2"


def test_get_user_by_id_missing_returns_none(fake_mongo):
    assert persistence.get_user_by_id("no-existe") is None


# ===================================================================
# Suscripciones
# ===================================================================


def test_create_and_list_subscriptions(fake_mongo):
    persistence.create_subscription(
        "u1", "fk1", "IB1234", "LEMD", "LEBL", "2026-09-01T10:00:00",
    )

    subs = persistence.list_subscriptions("u1")
    assert len(subs) == 1
    assert subs[0]["flight_key"] == "fk1"
    assert subs[0]["threshold_minutes"] == 60
    assert subs[0]["email"] is None


def test_create_subscription_custom_threshold_and_email(fake_mongo):
    persistence.create_subscription(
        "u1", "fk1", "IB1234", "LEMD", "LEBL", "2026-09-01T10:00:00",
        threshold_minutes=30, email="ana@example.com",
    )

    sub = persistence.get_subscription("u1", "fk1")
    assert sub is not None
    assert sub["threshold_minutes"] == 30
    assert sub["email"] == "ana@example.com"


def test_create_subscription_upsert_updates_not_duplicates(fake_mongo):
    persistence.create_subscription(
        "u1", "fk1", "IB1234", "LEMD", "LEBL", "2026-09-01T10:00:00",
    )
    persistence.create_subscription(
        "u1", "fk1", "IB1234", "LEMD", "LEBL", "2026-09-01T12:00:00",
        threshold_minutes=15,
    )

    subs = persistence.list_subscriptions("u1")
    assert len(subs) == 1
    assert subs[0]["schedule_local"] == "2026-09-01T12:00:00"
    assert subs[0]["threshold_minutes"] == 15


def test_delete_subscription(fake_mongo):
    persistence.create_subscription(
        "u1", "fk1", "IB1234", "LEMD", "LEBL", "2026-09-01T10:00:00",
    )

    assert persistence.delete_subscription("u1", "fk1") is True
    assert persistence.get_subscription("u1", "fk1") is None
    assert persistence.delete_subscription("u1", "fk1") is False


def test_subscriptions_isolated_per_user(fake_mongo):
    persistence.create_subscription(
        "u1", "fk1", "IB1234", "LEMD", "LEBL", "2026-09-01T10:00:00",
    )
    persistence.create_subscription(
        "u2", "fk1", "IB1234", "LEMD", "LEBL", "2026-09-01T10:00:00",
    )

    assert len(persistence.list_subscriptions("u1")) == 1
    assert len(persistence.list_subscriptions("u2")) == 1


# ===================================================================
# Alertas
# ===================================================================


def test_insert_and_list_alerts(fake_pg):
    alert_id = persistence.insert_alert(
        "u1", "fk1", "alta", 45.0, {"weather": True},
    )
    assert alert_id == 1

    alerts = persistence.list_alerts("u1")
    assert len(alerts) == 1
    assert alerts[0]["user_id"] == "u1"
    assert alerts[0]["severity"] == "alta"
    assert alerts[0]["delay_minutes_predicted"] == 45.0
    assert alerts[0]["email_sent"] is False
    assert alerts[0]["read"] is False


def test_list_alerts_filter_by_read(fake_pg):
    persistence.insert_alert("u1", "fk1", "alta", 45.0, {"weather": True})

    unread = persistence.list_alerts("u1", read=False)
    assert len(unread) == 1
    read = persistence.list_alerts("u1", read=True)
    assert len(read) == 0


def test_mark_alert_read(fake_pg):
    alert_id = persistence.insert_alert("u1", "fk1", "alta", 45.0, {"weather": True})

    assert persistence.mark_alert_read(alert_id) is True
