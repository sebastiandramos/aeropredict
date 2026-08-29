# Aeropredict — AGENTS.md

## Stack

- Python 3.12, Conda env at `.conda/envs/aeropredict/`, pip-installable (`pip install -e .`)
- Ruff lint: E/F/I/W/N/UP/B/C4/RUF, line-length 100
- Delta Lake (local `data/raw/`, R2 in CI), MongoDB (Silver), PostgreSQL (Gold)

## Pipeline (5 sequential scripts)

```
extract_opensky_to_bronze.py  → Delta Lake bronze/opensky
bronze_to_silver.py   → MongoDB collections flights, weather, aena_infovuelos, metar, holidays, eurocontrol_pru, notam
silver_to_gold.py     → PostgreSQL gold.{daily_airport_traffic,route_density,hourly_distribution}
silver_to_gold_entities.py → PostgreSQL gold.{flights,aircraft,weather,aena_infovuelos,metar,holidays,eurocontrol_pru,notam,airports,runways}
build_feature_store.py→ PostgreSQL gold.feature_store (ML features)
```

**CI** (`.github/workflows/pipeline.yml`): runs at 06:30/19:30 UTC, 5 sequential jobs, 30min/15min timeouts, R2 storage, 3 OpenSky client accounts. The `extract` job also runs Open-Meteo weather + the 5 keyless collectors with `continue-on-error: true` (OpenSky is CRITICAL — no `continue-on-error`; downstream jobs depend on flights). `.github/workflows/data-collectors.yml` is now lint-test only (daily 06:00 UTC).

**AENA hourly**: `collect_aena_infovuelos.py` runs hourly (separate workflow, cron minute 7 UTC) → Bronze `bronze/aena_infovuelos`. `bronze_to_silver.py` also promotes AENA Bronze → Silver using per-hour checkpoints (`checkpoints_bronze_to_silver_aena`), distinct from the OpenSky date checkpoint.

`silver_to_gold_entities.py` auto-reconcilia el unique key de `gold.aena_infovuelos` (5 columnas, incl. `scheduled_local`) al sincronizar AENA: si el constraint vigente es el antiguo de 4 columnas, migra automáticamente una vez por proceso. Fallback manual: `python scripts/migrate_aena_gold_unique.py --apply`.

**Data collectors**: the 5 keyless collectors run inside the `extract` job of `.github/workflows/pipeline.yml` (06:30/19:30 UTC) with `continue-on-error: true` (failure isolation); OpenSky is critical (no `continue-on-error`) and Open-Meteo weather also uses `continue-on-error`. Their data now flows Bronze → Silver (MongoDB) → Gold (PostgreSQL): `bronze_to_silver.py` promotes each Bronze table to Mongo (per-source checkpoints), `silver_to_gold_entities.py` syncs the collections to Gold (full sync, upsert/`ON CONFLICT`):
- `collect_metar.py` → `bronze/metar_awc` → Mongo `metar` → `gold.metar` (NOAA AWC)
- `collect_holidays.py` → `bronze/holidays_nager_date` + `bronze/holidays_python` → Mongo `holidays` → `gold.holidays` (Nager.Date + python-holidays; docs tagged `nager_date`/`python_holidays`)
- `collect_eurocontrol.py` → `bronze/eurocontrol_pru` → Mongo `eurocontrol_pru` → `gold.eurocontrol_pru` (EUROCONTROL PRU, `--year`; syncs with no field projection)
- `collect_ourairports.py` → `bronze/ourairports_airports` + `bronze/ourairports_runways` + Mongo `airports`/`runways` (dual-write at collect time) → `gold.airports`/`gold.runways` (OurAirports)
- `collect_notam.py` → `bronze/notam_enaire` → Mongo `notam` → `gold.notam` (ENAIRE servAIS; 401/403 → graceful exit 0 + warning)

All tables are registered in `TABLES_TO_SYNC` (`scripts/sync_r2_to_local.py`). `.github/workflows/data-collectors.yml` (cron daily 06:00 UTC, own `concurrency` group `data-collectors`) is now lint-test only: it runs `pytest tests/` + `ruff check scripts/ src/ tests/` (job `lint-test`) — the only CI job that runs the test suite.

## Code architecture (add a new data source)

- **`scripts/collect_*.py` are thin CLI wrappers** — each parses CLI flags, calls an adapter, and persists to Bronze via shared storage helpers (`aeropredict.opensky.storage`: `write_raw_csv`, `write_raw_snapshot`, `table_row_exists`). No HTTP/persistence logic lives in the script.
- **`src/aeropredict/sources/` is the adapter layer** — one module per external source (`metar.py`, `holidays.py`, `eurocontrol.py`, `ourairports.py`, `notam_enaire.py`, `openmeteo.py`, `aviationstack.py`, `aerodatabox.py`, `nager.py`, `python_holidays.py`, `airport_codes.py`, `airport_coords.py`). New data sources go here.
- **All adapters share `BaseAdapter` + `http_get_with_retry`/`http_post_with_retry`** from `src/aeropredict/sources/base.py` (exponential backoff + jitter, honors `Retry-After`, retries on 429/5xx/timeout/conn). Use these helpers — do not hand-roll `requests` calls. `NonJSONResponseError` (a 200-with-non-JSON body) is **intentionally NOT retried** (anti-bot/gateway page).
- **Test pattern for collectors**: tests in `tests/test_collect_*.py` `importlib`-load the script module from `scripts/` and `monkeypatch` the adapter's HTTP layer (e.g. `aeropredict.sources.metar.http_get_with_retry`) with fixture JSON — no network, no DB, no real Delta. Follow this pattern for new collectors.
- **A collector reaches Mint/R2 and then Silver/Gold automatically** once its Bronze table is registered in `TABLES_TO_SYNC` (`scripts/sync_r2_to_local.py`) and `bronze_to_silver.py` + `silver_to_gold_entities.py` are extended to promote/sync it (per-source checkpoint in `checkpoints_*`).

## Key Commands

```bash
# Run pipeline locally with Doppler secrets
doppler run -- python scripts/extract_opensky_to_bronze.py --days 2
doppler run -- python scripts/bronze_to_silver.py
doppler run -- python scripts/silver_to_gold.py
doppler run -- python scripts/silver_to_gold_entities.py
doppler run -- python scripts/build_feature_store.py

# Mock pipeline (no API calls, reads data/mock/opensky/*.json)
python scripts/mock_extract_to_bronze.py --days 2
python scripts/bronze_to_silver.py
python scripts/silver_to_gold_entities.py

# Generate mock data from real Bronze samples
python scripts/mock_extract_to_bronze.py --generate-samples --days 2

# Verify single step without side effects
python scripts/bronze_to_silver.py --date YYYY-MM-DD --dry-run
python scripts/silver_to_gold_entities.py --dry-run

# Docker local services
docker compose up -d  # MongoDB :27017, PostgreSQL :5432
```

## Dev Settings (override remote URIs for local Docker)

| Env var | Default (Docker local) |
|---|---|
| `MONGODB_URI` | `mongodb://localhost:27017/aeropredict` |
| `POSTGRES_URI` | `postgresql://aeropredict:aeropredict@localhost:5432/aeropredict` |
| `OPENSKY_DELTA_ROOT` | `data/raw` |

## Gotchas

- **PTY on WSL**: `pty_spawn` runs Windows `bash.exe`, NOT WSL. Use `wsl.exe -e /path/to/script.sh` to run in WSL.
- **Checkpoints in MongoDB**: `bronze_to_silver.py` uses per-source checkpoints (`checkpoints_*`, e.g. `checkpoints_bronze_to_silver_aena`) for idempotent promotion. `silver_to_gold_entities.py` uses a cursor checkpoint (collection `silver_to_gold_entities`) to resume reads, but writes are idempotent upserts/`ON CONFLICT` — safe to re-run for the whole set. Use `--force` to reset checkpoints.
- **OpenSky API**: ClientPool rotates accounts on HTTP 429. 5s delay between requests. 60s cap on Retry-After. `MIN_CREDITS=0` (never blocks on low credits).
- **Default bounding box**: Peninsular Spain + Balearics (`36.0,43.8,-9.3,4.3`). 42 Spanish airports + 12 European hubs in `AEROPUERTOS`.
- **Delta Lake partitioning**: `bronze/opensky` partitioned by `ingestion_date`, Silver tables by `flight_date`. Uses `mode="append"` — safe to re-run.
- **Dual-write**: When `delta_root` is local, auto-replicates to cloud if R2/S3 creds present, and vice versa.
- **Test suite**: `tests/` covers collectors, storage helpers and pipeline steps; run with `pytest tests/`. In CI only `data-collectors.yml` (job `lint-test`) runs it — `pipeline.yml` does not.
- **Logs**: written to `data/logs/daily_extract.log` (rotating 10MB, 5 backups). Set `OPENSKY_LOG_LEVEL` env var for DEBUG.
- **Weather + schedules**: separate collection scripts (`scripts/collect_weather.py`, `scripts/collect_schedules.py`) for non-OpenSky data sources (Open-Meteo, AviationStack, AeroDataBox).

## Environment creds

Secrets injected via Doppler (`doppler run`) or `.env` fallback. Expected vars:
- `OPENSKY_CLIENT_ID_{NAME}` / `OPENSKY_CLIENT_SECRET_{NAME}` — multiple accounts supported, pool rotates in 429
- `MONGODB_URI` / `POSTGRES_URI` — omit for Docker local defaults
- `OPENSKY_DELTA_ROOT` — `data/raw` (local), `s3://aeropredict-landing-zone` (CI/R2)

**Doppler CLI is REQUIRED to run almost any pipeline script.** There is **no `.env` file** with these secrets — they live only in Doppler (verified: `MONGODB_URI` is not in `.env`). Before running any `scripts/*.py` that touches Bronze/Silver/Gold, authenticate Doppler locally (`doppler login`, already configured as AeroPredict project) and prefix with `doppler run -- …`. Without it, the script will fail to build the Mongo/Postgres/Delta connections. Local Docker services (`docker compose up -d`) provide the DBs, but the *URIs/creds/pointers* still come from Doppler.
