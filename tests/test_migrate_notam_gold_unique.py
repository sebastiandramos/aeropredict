"""Tests de la migración del unique key de ``gold.notam`` (Task: fix NOTAM btree).

Cubre ``src/aeropredict/opensky/migrate_notam_gold_unique.py`` con cursores fake:
sin PostgreSQL, sin red.

Escenario real que motivó el fix: el índice UNIQUE legacy
``notam_layer_feature_json_key`` sobre ``(layer, feature_json)`` revienta con
``ProgramLimitExceeded`` cuando un feature GeoJSON supera 2704 B. La migración
debe pasar a ``(layer, feature_hash)`` sin perder el invariante de unicidad.
"""

from __future__ import annotations

from aeropredict.opensky import migrate_notam_gold_unique as mig

LEGACY_NAME = "notam_layer_feature_json_key"
LEGACY_DEF = (
    f"CREATE UNIQUE INDEX {LEGACY_NAME} ON gold.notam USING btree (layer, feature_json)"
)
TARGET_DEF = (
    f"CREATE UNIQUE INDEX {mig.NOTAM_TARGET_INDEX} "
    "ON gold.notam USING btree (layer, feature_hash)"
)


class _Cursor:
    """Cursor fake: registra el SQL ejecutado y simula fetchall por consulta."""

    def __init__(self, conn):
        self.conn = conn
        self._rows: list[list] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        self.conn.executed.append(sql)
        if "pg_index" in sql:
            self._rows = [list(row) for row in self.conn.index_defs]
        else:
            self._rows = []

    def fetchall(self):
        return self._rows


class _Conn:
    """Conexión fake con una lista configurable de índices UNIQUE."""

    def __init__(self, index_defs: list[tuple[str, str]]):
        self.index_defs = index_defs
        self.executed: list[str] = []
        self.commits = 0

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        self.commits += 1


# ------------------------------------------------------------------
# Constantes
# ------------------------------------------------------------------


def test_constants_match_expected_unique_key():
    assert mig.NOTAM_TARGET_INDEX == "notam_layer_feature_hash_key"
    assert mig.NOTAM_UNIQUE_COLUMNS == ("layer", "feature_hash")
    assert mig.GENERATED_EXPR == "md5(feature_json::text)"
    # md5() ya devuelve TEXT; un encode() daría error de tipos en PostgreSQL.
    assert "encode(" not in mig.GENERATED_EXPR


# ------------------------------------------------------------------
# Introspección
# ------------------------------------------------------------------


def test_get_notam_unique_index_names_returns_names():
    conn = _Conn([("a_key", "def a"), ("b_key", "def b")])
    assert mig.get_notam_unique_index_names(conn) == ["a_key", "b_key"]
    assert conn.executed == [mig.FIND_UNIQUE_INDEX_DEFS_SQL]


def test_get_notam_unique_index_defs_returns_pairs():
    conn = _Conn([("a_key", "def a"), ("b_key", "def b")])
    assert mig.get_notam_unique_index_defs(conn) == [("a_key", "def a"), ("b_key", "def b")]


# ------------------------------------------------------------------
# Idempotencia
# ------------------------------------------------------------------


def test_migrate_is_noop_when_target_index_present():
    conn = _Conn([(mig.NOTAM_TARGET_INDEX, TARGET_DEF)])
    mig.migrate_notam_gold_unique(conn)

    # Solo la consulta de introspección; ninguna DDL.
    assert conn.executed == [mig.FIND_UNIQUE_INDEX_DEFS_SQL]
    assert conn.commits == 0


def test_migrate_upgrades_legacy_index():
    conn = _Conn([(LEGACY_NAME, LEGACY_DEF)])
    mig.migrate_notam_gold_unique(conn)

    stmts = conn.executed
    assert stmts[0] == mig.FIND_UNIQUE_INDEX_DEFS_SQL
    assert mig.ADD_COLUMN_SQL in stmts
    assert mig.CREATE_UNIQUE_INDEX_SQL in stmts
    assert f'ALTER TABLE {mig.TABLE} DROP CONSTRAINT IF EXISTS "{LEGACY_NAME}"' in stmts
    assert f'DROP INDEX IF EXISTS gold."{LEGACY_NAME}"' in stmts


def test_migrate_creates_new_index_before_dropping_legacy():
    conn = _Conn([(LEGACY_NAME, LEGACY_DEF)])
    mig.migrate_notam_gold_unique(conn)

    stmts = conn.executed
    create_pos = stmts.index(mig.CREATE_UNIQUE_INDEX_SQL)
    drop_constraint_pos = stmts.index(
        f'ALTER TABLE {mig.TABLE} DROP CONSTRAINT IF EXISTS "{LEGACY_NAME}"'
    )
    drop_index_pos = stmts.index(f'DROP INDEX IF EXISTS gold."{LEGACY_NAME}"')

    # El invariante de unicidad nunca se pierde: nuevo índice antes de dropear.
    assert create_pos < drop_constraint_pos
    assert create_pos < drop_index_pos


def test_migrate_does_not_drop_unrelated_unique_index():
    unrelated = (
        "notam_other_key",
        "CREATE UNIQUE INDEX notam_other_key ON gold.notam USING btree (layer)",
    )
    conn = _Conn([unrelated])
    mig.migrate_notam_gold_unique(conn)

    assert mig.CREATE_UNIQUE_INDEX_SQL in conn.executed
    assert not any("notam_other_key" in stmt for stmt in conn.executed if "DROP" in stmt)


def test_migrate_handles_multiple_legacy_indices():
    second = "notam_feature_json_alt_key"
    second_def = (
        f"CREATE UNIQUE INDEX {second} ON gold.notam USING btree (layer, feature_json)"
    )
    conn = _Conn(
        [
            (LEGACY_NAME, LEGACY_DEF),
            (second, second_def),
        ]
    )
    mig.migrate_notam_gold_unique(conn)

    for name in (LEGACY_NAME, second):
        assert f'ALTER TABLE {mig.TABLE} DROP CONSTRAINT IF EXISTS "{name}"' in conn.executed
        assert f'DROP INDEX IF EXISTS gold."{name}"' in conn.executed
