# aeropredict

TFM — Predicción de retrasos de vuelos.

## Pipeline de datos

### Flujo completo (producción)

```
GitHub Actions (cron 06:30 / 19:30 UTC; AENA: cron horario minuto 7 UTC; data-collectors: cron diario 06:00 UTC)
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
bronze_to_silver.py → Silver (MongoDB Atlas) — flights + weather + aena_infovuelos
  ↓
silver_to_gold.py          → Gold (PostgreSQL — Neon) — agregaciones de vuelos
silver_to_gold_entities.py → Gold (PostgreSQL — Neon) — tablas entidad raw
build_feature_store.py     → Gold (PostgreSQL — Neon) — feature store
```

### Colectores de datos complementarios (workflow `data-collectors.yml`)

Fuentes sin API key, escritura directa a Bronze (R2). El workflow corre un paso por
collector (aislamiento de fallos) más un job `lint-test` (`pytest tests/` + `ruff check`):

| Script | Bronze table(s) | Fuente |
|---|---|---|
| `scripts/collect_metar.py` | `bronze/metar_awc` | NOAA Aviation Weather Center |
| `scripts/collect_holidays.py` | `bronze/holidays_nager_date`, `bronze/holidays_python` | Nager.Date + python-holidays |
| `scripts/collect_eurocontrol.py` | `bronze/eurocontrol_pru` | EUROCONTROL PRU (CSV anual, `--year`) |
| `scripts/collect_ourairports.py` | `bronze/ourairports_airports`, `bronze/ourairports_runways` | OurAirports (Unlicense) |
| `scripts/collect_notam.py` | `bronze/notam_enaire` | ENAIRE servAIS (ArcGIS FeatureServer) |

Todas las tablas soportan `--dry-run`, no requieren claves y están registradas en
`TABLES_TO_SYNC` de `scripts/sync_r2_to_local.py` (incluida `bronze/aena_infovuelos`).

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

Lee los datos de Bronze y los inserta en MongoDB (colecciones `flights`, `weather` y `aena_infovuelos`; esta última se promueve por horas, con checkpoint independiente).

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

Lee las 4 colecciones de MongoDB y las escribe en tablas Gold en PostgreSQL.

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
| `aena_infovuelos` | `gold.aena_infovuelos` | Horaria | `SERIAL` + UNIQUE `(snapshot_at_utc, flight_number, aena_airport_iata, flight_type)` |

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
