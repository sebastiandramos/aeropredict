"""Migración del unique key de ``gold.aena_infovuelos`` (lógica reutilizable).

Añade ``scheduled_local`` al constraint UNIQUE existente, que actualmente es
``(snapshot_at_utc, flight_number, aena_airport_iata, flight_type)`` y provoca que
los vuelos recurrentes (mismo número de vuelo en un mismo snapshot con distinta
fecha programada) colisionen y se pierdan en el ``ON CONFLICT ... DO NOTHING``.

Nuevo unique key: ``(snapshot_at_utc, flight_number, aena_airport_iata,
flight_type, scheduled_local)``.

Seguridad:
- No asume el nombre autogenerado de la constraint: lo busca en ``pg_constraint``.
- Verifica que no haya duplicados según el NUEVO key antes de alterar.
- Idempotente: si el unique de 5 columnas ya existe, no altera nada.

Por qué NO se necesita dedupe previo: el unique de 4 columnas ya vigente en
producción impide que existan dos filas con el mismo
``(snapshot_at_utc, flight_number, aena_airport_iata, flight_type)``; como el
nuevo key es un superconjunto del antiguo, no puede haber duplicados según el
nuevo key mientras el antiguo esté aplicado. El chequeo de duplicados se
mantiene como red de seguridad para el caso patológico de tabla sin constraint.

El CLI ``scripts/migrate_aena_gold_unique.py`` es un thin wrapper sobre este
módulo (convención del repo: los scripts solo parsean flags y delegan).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

TABLE = "gold.aena_infovuelos"
AENA_UNIQUE_COLUMNS = (
    "snapshot_at_utc",
    "flight_number",
    "aena_airport_iata",
    "flight_type",
    "scheduled_local",
)

DUP_CHECK_SQL = f"""
SELECT COUNT(*) AS dupes
FROM (
    SELECT {", ".join(AENA_UNIQUE_COLUMNS)}
    FROM {TABLE}
    GROUP BY {", ".join(AENA_UNIQUE_COLUMNS)}
    HAVING COUNT(*) > 1
) AS d;
"""

# Columnas de cada constraint UNIQUE de gold.aena_infovuelos, ordenadas por la
# posición de la columna dentro del key (array_position sobre conkey).
FIND_UNIQUE_COLUMNS_SQL = """
SELECT con.conname, att.attname
FROM pg_constraint con
JOIN pg_class rel ON rel.oid = con.conrelid
JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
JOIN pg_attribute att ON att.attrelid = con.conrelid
                        AND att.attnum = ANY (con.conkey)
WHERE con.contype = 'u'
  AND nsp.nspname = 'gold'
  AND rel.relname = 'aena_infovuelos'
ORDER BY con.conname, array_position(con.conkey, att.attnum);
"""


def get_aena_unique_constraints(conn: Any) -> list[tuple[str, list[str]]]:
    """Devuelve ``(nombre, columnas)`` de cada constraint UNIQUE de gold.aena_infovuelos.

    Vacío si la tabla no tiene ningún constraint UNIQUE.
    """
    with conn.cursor() as cur:
        cur.execute(FIND_UNIQUE_COLUMNS_SQL)
        rows = cur.fetchall()
    by_name: dict[str, list[str]] = {}
    for name, column in rows:
        by_name.setdefault(name, []).append(column)
    return list(by_name.items())


def count_aena_duplicates(conn: Any) -> int:
    """Cuenta filas duplicadas según el nuevo unique key (5 columnas)."""
    with conn.cursor() as cur:
        cur.execute(DUP_CHECK_SQL)
        row = cur.fetchone()
        return int(row[0]) if row else 0


def migrate_aena_gold_unique(conn: Any) -> None:
    """Aplica la migración: dropea los UNIQUE existentes y crea el de 5 columnas.

    Idempotente: si el unique de 5 columnas ya existe, no altera nada.

    Raises:
        ValueError: si hay duplicados según el nuevo key (abortar antes de alterar).
    """
    dupes = count_aena_duplicates(conn)
    logger.info("Duplicados según nuevo key %s: %d", AENA_UNIQUE_COLUMNS, dupes)
    if dupes:
        raise ValueError(
            f"Hay {dupes} duplicados según el nuevo key {AENA_UNIQUE_COLUMNS}. "
            "Revisar antes de migrar."
        )

    constraints = get_aena_unique_constraints(conn)
    if any(cols == list(AENA_UNIQUE_COLUMNS) for _, cols in constraints):
        logger.info(
            "gold.aena_infovuelos ya tiene el unique key de 5 columnas. Nada que hacer."
        )
        return

    with conn.cursor() as cur:
        for name, _cols in constraints:
            logger.info("Dropeando constraint UNIQUE %s...", name)
            cur.execute(f'ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS "{name}"')

        new_cols_sql = ", ".join(AENA_UNIQUE_COLUMNS)
        logger.info("Creando nuevo UNIQUE (%s)...", new_cols_sql)
        cur.execute(
            f"ALTER TABLE {TABLE} ADD CONSTRAINT uq_aena_infovuelos_unique "
            f"UNIQUE ({new_cols_sql})"
        )
    logger.info("Migración aplicada correctamente.")
