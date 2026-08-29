# aeropredict

TFM — Predicción de retrasos de vuelos.

## Pipeline de datos

### Flujo completo (producción)

```
GitHub Actions (cron 06:30 / 19:30 UTC; AENA: cron horario minuto 7 UTC; data-collectors: lint+test cron diario 06:00 UTC)
  ↓
extract_opensky_to_bronze.py → Bronze (R2)
collect_weather.py           → Bronze (R2) — Open-Meteo weather
collect_aena_infovuelos.py   → Bronze (R2) — AENA infovuelos (horario)
collect_metar.py             → Bronze (R2) — METAR (NOAA AWC)
collect_holidays.py          → Bronze (R2) — festivos España (Nager.Date + python-holidays)
collect_eurocontrol.py       → Bronze (R2) — EUROCONTROL PRU (CSV anual)
collect_ourairports.py       → Bronze (R2) — OurAirports (airports + runways)
collect_notam.py             → Bronze (R2) — NOTAM (ENAIRE servAIS)
  ↓
bronze_to_silver.py → Silver (MongoDB Atlas) — flights, weather, aena_infovuelos, metar, holidays, eurocontrol_pru, notam
  ↓
silver_to_gold.py          → Gold (PostgreSQL — Neon) — agregaciones de vuelos
silver_to_gold_entities.py → Gold (PostgreSQL — Neon) — tablas entidad raw
build_feature_store.py     → Gold (PostgreSQL — Neon) — feature store
```

### Colectores de datos complementarios (job `extract` de `pipeline.yml`)

Fuentes sin API key. Escriben a Bronze (R2) y su flujo continúa hasta Gold:
`bronze_to_silver.py` promueve cada Bronze a Silver (MongoDB) y
`silver_to_gold_entities.py` sincroniza las colecciones a tablas Gold
(PostgreSQL). Corren en el job `extract` de `pipeline.yml` (06:30/19:30 UTC)
con `continue-on-error: true` (aislamiento de fallos: una fuente caída no
bloquea al resto). El workflow `data-collectors.yml` ahora solo corre
`lint-test` (`pytest tests/` + `ruff check`) cada día a las 06:00 UTC:

| Script | Bronze table(s) | Colección MongoDB (Silver) | Tabla Gold | Fuente |
|---|---|---|---|---|
| `scripts/collect_metar.py` | `bronze/metar_awc` | `metar` | `gold.metar` | NOAA Aviation Weather Center |
| `scripts/collect_holidays.py` | `bronze/holidays_nager_date`, `bronze/holidays_python` | `holidays` | `gold.holidays` | Nager.Date + python-holidays |
| `scripts/collect_eurocontrol.py` | `bronze/eurocontrol_pru` | `eurocontrol_pru` | `gold.eurocontrol_pru` | EUROCONTROL PRU (CSV anual, `--year`) |
| `scripts/collect_ourairports.py` | `bronze/ourairports_airports`, `bronze/ourairports_runways` | `airports`, `runways` (dual-write al recoger) | `gold.airports`, `gold.runways` | OurAirports (Unlicense) |
| `scripts/collect_notam.py` | `bronze/notam_enaire` | `notam` | `gold.notam` | ENAIRE servAIS (ArcGIS FeatureServer) |

Todas las tablas soportan `--dry-run`, no requieren claves y están registradas en
`TABLES_TO_SYNC` de `scripts/sync_r2_to_local.py` (incluida `bronze/aena_infovuelos`).
`collect_ourairports.py` además hace dual-write al recoger: escribe directamente
las colecciones MongoDB `airports`/`runways`, que `bronze_to_silver.py` no
promueve porque ya están en Silver.

### Flujo mock (desarrollo local, sin OpenSky API)

Usa datos sintéticos locales en lugar de llamar a la API de OpenSky.

```
data/mock/opensky/{date}/{ICAO}_{arrivals|departures}.json
  ↓
python scripts/mock_extract_to_bronze.py [--days N]
  ↓
python scripts/bronze_to_silver.py [--date YYYY-MM-DD]
  ↓
python scripts/silver_to_gold_entities.py [--dry-run]
```

#### 1. Mock extract → Bronze

Samplea datos reales desde Bronze (Delta Lake) y los guarda como JSON mock.

```bash
# Extraer (usando los mock) los últimos 2 días
python scripts/mock_extract_to_bronze.py --days 2

# Dry-run: muestra qué archivos se procesarían
python scripts/mock_extract_to_bronze.py --days 2 --dry-run

# Rango de fechas concreto
python scripts/mock_extract_to_bronze.py --start 2025-01-15 --end 2025-01-16
```

Parámetros:

| Flag | Default | Descripción |
|---|---|---|
| `--days` | `2` | Días hacia atrás desde hoy |
| `--start` | — | Fecha inicio (YYYY-MM-DD), anula `--days` |
| `--end` | — | Fecha fin (YYYY-MM-DD) |
| `--airports` | `LEMD,LEBL,LEAL` | Códigos ICAO separados por coma |
| `--mock-dir` | `data/mock` | Directorio con los JSON mock |
| `--dry-run` | — | Solo listar archivos, no escribir |


#### 2. Bronze → Silver (MongoDB)

Lee los datos de Bronze y los inserta en MongoDB: siete colecciones (`flights`, `weather`, `aena_infovuelos`, `metar`, `holidays`, `eurocontrol_pru` y `notam`). Cada fuente usa su propio checkpoint en `checkpoints_*`: `flights` por día, `aena_infovuelos` y `metar` por hora, `holidays` por `{source}_{year}`, `eurocontrol_pru` por `{filename}_{year}` y `notam` por `snapshot_at`.

```bash
# Fecha concreta
python scripts/bronze_to_silver.py --date 2025-01-15

# Rango
python scripts/bronze_to_silver.py --start 2025-01-15 --end 2025-01-16

# Sin fecha: procesa todos los datos pendientes en Bronze
python scripts/bronze_to_silver.py

# Dry-run
python scripts/bronze_to_silver.py --date 2025-01-15 --dry-run
```

| Flag | Default | Descripción |
|---|---|---|
| `--date` | — | Fecha concreta (YYYY-MM-DD) |
| `--start` | — | Fecha inicio del rango |
| `--end` | — | Fecha fin del rango |
| `--dry-run` | — | Mostrar vuelos parseados sin insertar |

#### 3. Silver → Gold entidades (PostgreSQL)

Lee las 10 colecciones de MongoDB y las escribe en tablas Gold en PostgreSQL (sincronización completa; las escrituras usan upsert / `ON CONFLICT`, seguras de re-ejecutar).

```bash
# Sincronizar todo
python scripts/silver_to_gold_entities.py

# Ver stats sin insertar
python scripts/silver_to_gold_entities.py --dry-run
```

**Tablas Gold generadas:**

| Colección MongoDB | Tabla PostgreSQL | Tipo | PK |
|---|---|---|---|
| `flights` | `gold.flights` | Raw (tabular) | `SERIAL` + índices |
| `aircraft` | `gold.aircraft` | Maestra | `icao24` |
| `weather` | `gold.weather` | Horaria | `SERIAL` + índice `(airport_code, flight_date)` |
| `aena_infovuelos` | `gold.aena_infovuelos` | Horaria | `SERIAL` + UNIQUE `(snapshot_at_utc, flight_number, aena_airport_iata, flight_type, scheduled_local)` |
| `metar` | `gold.metar` | Meteorológica (METAR) | `SERIAL` + UNIQUE `(icao_id, obs_time)` |
| `holidays` | `gold.holidays` | Calendario | `SERIAL` + UNIQUE `(date, name, country_code, source, subdivision)` |
| `eurocontrol_pru` | `gold.eurocontrol_pru` | Operacional (PRU) | `SERIAL` + UNIQUE `(source_file, year, row_json)` |
| `notam` | `gold.notam` | NOTAM | `SERIAL` + UNIQUE `(layer, feature_json)` |
| `airports` | `gold.airports` | Maestra | `ident` |
| `runways` | `gold.runways` | Maestra | `(airport_ident, le_ident, he_ident)` |

> **AENA unique key**: `silver_to_gold_entities.py` auto-reconcilia el UNIQUE de
> `gold.aena_infovuelos` (5 columnas, incl. `scheduled_local`) al sincronizar
> AENA: si el constraint vigente es el antiguo de 4 columnas, migra
> automáticamente una vez por proceso. Fallback manual:
> `python scripts/migrate_aena_gold_unique.py --apply`.

### Pipeline completo mock (un solo comando)

```bash
# Extraer datos mock de los últimos 2 días
python scripts/mock_extract_to_bronze.py --days 2

# Subir todo lo pendiente a MongoDB
python scripts/bronze_to_silver.py

# Sincronizar entidades a PostgreSQL
python scripts/silver_to_gold_entities.py
```

### Requisitos

- Python 3.12+
- MongoDB Atlas (o local) — `MONGODB_URI` en Doppler
- PostgreSQL Neon (o local) — `POSTGRES_URI` en Doppler
- Paquete instalado: `pip install -e .`
