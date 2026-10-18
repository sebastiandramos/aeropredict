"""Migración del unique key de ``gold.notam`` (lógica reutilizable).

La constraint original es ``UNIQUE (layer, feature_json)``: un índice btree sobre
una columna JSONB. PostgreSQL limita cada entrada de un índice btree a 2704 B, y
los features GeoJSON de NOTAM no tienen tamaño acotado, así que un feature grande
rompe el INSERT con ``psycopg2.errors.ProgramLimitExceeded``.

La solución indexa el hash MD5 del JSON en lugar del JSON completo:

- Columna generada ``feature_hash TEXT GENERATED ALWAYS AS
  (md5(feature_json::text)) STORED`` (la rellena PostgreSQL, sin backfill).
- Constraint/índice UNIQUE ``(layer, feature_hash)``.

La semántica de deduplicación se preserva exactamente: dos features iguales
producen el mismo ``feature_json`` y por tanto el mismo ``md5``. El índice sobre
el hash ocupa un tamaño fijo (32 B de texto), sin límite de tamaño.

Seguridad:
- No asume el nombre autogenerado: inspecciona ``pg_index`` para descubrir qué
  índices UNIQUE existen realmente.
- Crea el NUEVO unique index ANTES de dropear el antiguo: el invariante de
  unicidad nunca se pierde durante la migración.
- Idempotente: si el índice de ``(layer, feature_hash)`` ya existe, no altera nada.
- Sin dedupe previo: mientras el unique antiguo ``(layer, feature_json)`` esté
  vigente no puede haber dos filas con el mismo ``feature_json`` (y por tanto
  mismo hash), así que crear el nuevo índice no puede fallar por duplicados.

El CLI ``scripts/migrate_notam_gold_unique.py`` es un thin wrapper sobre este
módulo (convención del repo: los scripts solo parsean flags y delegan).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

TABLE = "gold.notam"
FEATURE_HASH_COLUMN = "feature_hash"
NOTAM_UNIQUE_COLUMNS = ("layer", FEATURE_HASH_COLUMN)

# Nombre del índice UNIQUE objetivo. Coincide con el que genera PostgreSQL para
# ``UNIQUE (layer, feature_hash)`` declarado inline en el CREATE TABLE, de modo
# que instalaciones nuevas y bases migradas comparten el mismo nombre.
NOTAM_TARGET_INDEX = "notam_layer_feature_hash_key"

# Columna generada: md5() ya devuelve TEXT (32 chars hex), no hace falta encode().
GENERATED_EXPR = "md5(feature_json::text)"

ADD_COLUMN_SQL = (
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
    f"{FEATURE_HASH_COLUMN} TEXT GENERATED ALWAYS AS ({GENERATED_EXPR}) STORED"
)

CREATE_UNIQUE_INDEX_SQL = (
    f"CREATE UNIQUE INDEX IF NOT EXISTS {NOTAM_TARGET_INDEX} "
    f"ON {TABLE} ({', '.join(NOTAM_UNIQUE_COLUMNS)})"
)

# Índices UNIQUE (no primarios) de gold.notam: nombre + definición completa.
FIND_UNIQUE_INDEX_DEFS_SQL = """
SELECT c.relname, pg_get_indexdef(c.oid) AS indexdef
FROM pg_class c
JOIN pg_index ix ON ix.indexrelid = c.oid
JOIN pg_class t ON t.oid = ix.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE ix.indisunique
  AND NOT ix.indisprimary
  AND n.nspname = 'gold'
  AND t.relname = 'notam'
ORDER BY c.relname;
"""


def get_notam_unique_index_defs(conn: Any) -> list[tuple[str, str]]:
    """Devuelve ``(nombre, definición)`` de cada índice UNIQUE de gold.notam."""
    with conn.cursor() as cur:
        cur.execute(FIND_UNIQUE_INDEX_DEFS_SQL)
        return [(row[0], row[1]) for row in cur.fetchall()]


def get_notam_unique_index_names(conn: Any) -> list[str]:
    """Nombres de los índices UNIQUE (no primarios) de gold.notam."""
    return [name for name, _ in get_notam_unique_index_defs(conn)]


def migrate_notam_gold_unique(conn: Any) -> None:
    """Migra ``gold.notam`` al unique key ``(layer, feature_hash)``.

    Idempotente: si el índice objetivo ya existe, no altera nada.

    Orden de operaciones (el invariante de unicidad nunca se pierde):
    1. Añade la columna generada ``feature_hash`` (rellenada por PostgreSQL).
    2. Crea el nuevo índice UNIQUE ``(layer, feature_hash)``.
    3. Dropea el/los índice(s) UNIQUE legacy que indexen ``feature_json``.
    """
    defs = get_notam_unique_index_defs(conn)
    names = [name for name, _ in defs]
    if NOTAM_TARGET_INDEX in names:
        logger.info("gold.notam ya tiene el unique key (layer, feature_hash). Nada que hacer.")
        return

    with conn.cursor() as cur:
        logger.info("Añadiendo columna generada %s...", FEATURE_HASH_COLUMN)
        cur.execute(ADD_COLUMN_SQL)

        logger.info(
            "Creando índice UNIQUE %s (%s)...",
            NOTAM_TARGET_INDEX,
            ", ".join(NOTAM_UNIQUE_COLUMNS),
        )
        cur.execute(CREATE_UNIQUE_INDEX_SQL)

        legacy = [(name, indexdef) for name, indexdef in defs if "feature_json" in indexdef]
        for name, _indexdef in legacy:
            logger.info("Dropeando índice/constraint UNIQUE legacy %s...", name)
            # Si el índice respalda una constraint, hay que dropear la constraint;
            # si es un índice suelto, basta con DROP INDEX. Ambos son idempotentes.
            cur.execute(f'ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS "{name}"')
            cur.execute(f'DROP INDEX IF EXISTS gold."{name}"')

    logger.info("Migración aplicada correctamente.")
