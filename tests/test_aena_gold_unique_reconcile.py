"""Tests de la reconciliación automática del unique key de ``gold.aena_infovuelos``.

Cubre:
- ``storage_gold._reconcile_aena_gold_unique``: constraint 4-col → migra una vez
  por proceso y el write procede; constraint 5-col → no-op; fallo → error claro
  con el fallback manual.
- ``aeropredict.opensky.migrate_aena_gold_unique``: consulta de constraints,
  conteo de duplicados y migración (drop + add), idempotencia.
- CLI ``scripts/migrate_aena_gold_unique.py``: ``--apply`` / ``--dry-run``
  intactos.

Todo con mocks puros: sin PostgreSQL, sin red, sin Doppler.
"""

import importlib.util
from pathlib import Path

import pytest

from aeropredict.opensky import migrate_aena_gold_unique as mig
from aeropredict.opensky import storage_gold

OLD_4COL = [
    "snapshot_at_utc",
    "flight_number",
    "aena_airport_iata",
    "flight_type",
]
NEW_5COL = [
    "snapshot_at_utc",
    "flight_number",
    "aena_airport_iata",
    "flight_type",
    "scheduled_local",
]


# ------------------------------------------------------------------
# Fakes de conexión/cursor (sin PostgreSQL real)
# ------------------------------------------------------------------


class FakeCursor:
    """Cursor no-op que registra las sentencias ejecutadas en la conexión."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))

    def fetchone(self):
        return self.conn.fetchone_result

    def fetchall(self):
        return self.conn.fetchall_result


class FakeConn:
    """Conexión fake: registra SQL, devuelve resultados fijos y no-op commit."""

    def __init__(self, fetchall_result=None, fetchone_result=None):
        self.executed: list[tuple] = []
        self.fetchall_result = fetchall_result
        self.fetchone_result = fetchone_result
        self.commits = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def close(self):
        pass


def _aena_doc(flight_number, airport, **overrides):
    doc = {
        "snapshot_at_utc": "2026-08-03T10:00:00Z",
        "flight_number": flight_number,
        "aena_airport_iata": airport,
        "flight_type": "departures",
        "source": "aena_infovuelos",
        "query_airport_iata": "MAD",
        "query_flight_type": "departures",
        "raw_flight_number": "1234",
        "airline_iata": "IB",
        "airline_icao": "IBE",
        "airline_name": "Iberia",
        "icao24_airport": None,
        "other_airport_iata": "BCN",
        "other_city": "Barcelona",
        "scheduled_date": "03/08/2026",
        "scheduled_time": "12:00:00",
        "scheduled_local": "2026-08-03T12:00:00",
        "estimated_date": None,
        "estimated_time": None,
        "estimated_local": None,
        "status": "Programado",
        "terminal": "T4",
        "gate_first": None,
        "gate_second": None,
        "checkin_from": None,
        "checkin_to": None,
        "aircraft_type": None,
    }
    doc.update(overrides)
    return doc


@pytest.fixture(autouse=True)
def _reset_aena_cache():
    """La cache de reconciliación es global por proceso: resetear en cada test."""
    storage_gold._aena_unique_reconciled = False
    yield
    storage_gold._aena_unique_reconciled = False


def _patch_writer(monkeypatch, conn, constraints, migrate_impl=None):
    """Mockea la conexión, la consulta de constraints y la migración del writer."""
    monkeypatch.setattr(storage_gold, "_get_conn", lambda: conn)
    monkeypatch.setattr(
        storage_gold,
        "get_aena_unique_constraints",
        lambda c: constraints,
    )
    calls: list = []
    if migrate_impl is None:
        migrate_impl = lambda c: calls.append(c)  # noqa: E731
    monkeypatch.setattr(storage_gold, "migrate_aena_gold_unique", migrate_impl)
    monkeypatch.setattr(storage_gold, "execute_values", lambda *a, **k: None)
    return calls


# ------------------------------------------------------------------
# storage_gold._reconcile_aena_gold_unique (vía write_aena_infovuelos_gold)
# ------------------------------------------------------------------


def test_reconcile_migrates_when_4col_and_write_proceeds(monkeypatch):
    conn = FakeConn()
    calls = _patch_writer(
        monkeypatch,
        conn,
        [("uq_aena_old", OLD_4COL)],
    )

    n = storage_gold.write_aena_infovuelos_gold([_aena_doc("IB1234", "MAD")])

    assert n == 1
    assert calls == [conn]  # migración invocada una vez con la conexión del writer
    assert conn.commits == 1  # el write procede


def test_reconcile_runs_once_per_process(monkeypatch):
    conn = FakeConn()
    calls = _patch_writer(monkeypatch, conn, [("uq_aena_old", OLD_4COL)])

    storage_gold.write_aena_infovuelos_gold([_aena_doc("IB1234", "MAD")])
    storage_gold.write_aena_infovuelos_gold([_aena_doc("IB1235", "MAD")])

    assert len(calls) == 1  # cache por proceso: no se consulta/migra en cada write


def test_reconcile_noop_when_5col(monkeypatch):
    conn = FakeConn()
    calls = _patch_writer(
        monkeypatch,
        conn,
        [("uq_aena_infovuelos_unique", NEW_5COL)],
    )

    n = storage_gold.write_aena_infovuelos_gold([_aena_doc("IB1234", "MAD")])

    assert n == 1
    assert calls == []  # constraint ya correcto → no se invoca la migración


def test_reconcile_failure_raises_clear_error_with_fallback(monkeypatch):
    conn = FakeConn()

    def boom(c):
        raise ValueError("duplicados según el nuevo key")

    _patch_writer(monkeypatch, conn, [("uq_aena_old", OLD_4COL)], migrate_impl=boom)

    with pytest.raises(RuntimeError, match=r"migrate_aena_gold_unique\.py --apply"):
        storage_gold.write_aena_infovuelos_gold([_aena_doc("IB1234", "MAD")])


def test_reconcile_not_called_for_empty_docs(monkeypatch):
    def fail_if_called(*a, **k):
        raise AssertionError("con lista vacía no debe conectar a PostgreSQL")

    monkeypatch.setattr(storage_gold, "_get_conn", fail_if_called)

    assert storage_gold.write_aena_infovuelos_gold([]) == 0


# ------------------------------------------------------------------
# aeropredict.opensky.migrate_aena_gold_unique (librería)
# ------------------------------------------------------------------


def test_get_aena_unique_constraints_groups_columns_by_name():
    conn = FakeConn(
        fetchall_result=[
            ("uq_aena_old", "snapshot_at_utc"),
            ("uq_aena_old", "flight_number"),
            ("uq_aena_old", "aena_airport_iata"),
            ("uq_aena_old", "flight_type"),
        ]
    )

    constraints = mig.get_aena_unique_constraints(conn)

    assert constraints == [("uq_aena_old", OLD_4COL)]


def test_get_aena_unique_constraints_empty_when_no_unique():
    conn = FakeConn(fetchall_result=[])

    assert mig.get_aena_unique_constraints(conn) == []


def test_count_aena_duplicates_returns_count():
    conn = FakeConn(fetchone_result=(3,))

    assert mig.count_aena_duplicates(conn) == 3


def test_migrate_aena_gold_unique_drops_old_and_adds_5col():
    conn = FakeConn(
        fetchone_result=(0,),
        fetchall_result=[
            ("uq_aena_old", "snapshot_at_utc"),
            ("uq_aena_old", "flight_number"),
            ("uq_aena_old", "aena_airport_iata"),
            ("uq_aena_old", "flight_type"),
        ],
    )

    mig.migrate_aena_gold_unique(conn)

    sqls = [sql for sql, _ in conn.executed]
    assert any(
        "DROP CONSTRAINT IF EXISTS" in sql and "uq_aena_old" in sql for sql in sqls
    )
    assert any(
        "ADD CONSTRAINT uq_aena_infovuelos_unique" in sql and "scheduled_local" in sql
        for sql in sqls
    )


def test_migrate_aena_gold_unique_noop_when_5col_already_present():
    conn = FakeConn(
        fetchone_result=(0,),
        fetchall_result=[("uq_aena_infovuelos_unique", col) for col in NEW_5COL],
    )

    mig.migrate_aena_gold_unique(conn)

    sqls = [sql for sql, _ in conn.executed]
    assert not any("ALTER TABLE" in sql for sql in sqls)


def test_migrate_aena_gold_unique_raises_on_duplicates():
    conn = FakeConn(fetchone_result=(2,))

    with pytest.raises(ValueError, match="duplicados"):
        mig.migrate_aena_gold_unique(conn)


# ------------------------------------------------------------------
# CLI scripts/migrate_aena_gold_unique.py (--apply / --dry-run)
# ------------------------------------------------------------------


def _load_migrate_script():
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "migrate_aena_gold_unique.py"
    )
    spec = importlib.util.spec_from_file_location(
        "migrate_aena_gold_unique_module", module_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _patch_cli(monkeypatch, module, conn, dupes=0, constraints=None):
    monkeypatch.setattr(module.psycopg2, "connect", lambda uri: conn)
    monkeypatch.setattr(module, "count_aena_duplicates", lambda c: dupes)
    monkeypatch.setattr(
        module,
        "get_aena_unique_constraints",
        lambda c: constraints if constraints is not None else [],
    )
    calls: list = []
    monkeypatch.setattr(module, "migrate_aena_gold_unique", lambda c: calls.append(c))
    return calls


def test_migrate_cli_dry_run_does_not_migrate(monkeypatch):
    module = _load_migrate_script()
    conn = FakeConn()
    calls = _patch_cli(
        monkeypatch,
        module,
        conn,
        constraints=[("uq_aena_old", OLD_4COL)],
    )

    rc = module.main([])  # dry-run por defecto

    assert rc == 0
    assert calls == []


def test_migrate_cli_apply_calls_migrate(monkeypatch):
    module = _load_migrate_script()
    conn = FakeConn()
    calls = _patch_cli(
        monkeypatch,
        module,
        conn,
        constraints=[("uq_aena_old", OLD_4COL)],
    )

    rc = module.main(["--apply"])

    assert rc == 0
    assert calls == [conn]


def test_migrate_cli_aborts_on_duplicates(monkeypatch):
    module = _load_migrate_script()
    conn = FakeConn()
    _patch_cli(monkeypatch, module, conn, dupes=2)

    rc = module.main(["--apply"])

    assert rc == 1
