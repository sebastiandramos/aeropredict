# aeropredict-gap-closure - Work Plan

## TL;DR (For humans)

**What you'll get:** A complete test suite (pytest with 70+ tests covering data pipeline, data quality, and feature store), a trained ML model (LightGBM delay predictor with MLflow tracking), an inference API (FastAPI endpoint returning ETA + delay predictions), a prediction archive + evaluation pipeline (stores predictions and compares to actual arrivals), and an enterprise visualization layer (Grafana dashboards with live SQL queries). **Closes RF-F01, RF-F02, RF-F03, RF-F04, RF-F05, RF-F06, RF-F07, RF-F08 and all RNF constraints.** Schedule data is populated by your teammate via web scraping.

**Why this approach:** The project has a solid **data pipeline** (Extract → Bronze/Silver/Gold is fully working) but zero testing and zero ML. Best practice: lock the pipeline with tests first (prevent regressions), then build ML in isolation with experiment tracking, then expose via API with prediction archival (for RF-F07), then add minimal visualization dashboard (Streamlit for RF-F06). Parallel execution in 4 waves + Phase 2 visualization keeps effort manageable.

**What it will NOT do:** This plan does NOT deploy to production, does NOT set up Airflow/Kubernetes (GitHub Actions cron is sufficient for now), does NOT build dashboards (that's Phase 2), does NOT optimize query performance beyond current indexes, and does NOT audit the OpenSky API account usage. Those are future work.

**Effort:** **XL** (4 phases, ~250-300 tasks equivalent across a 4-week timeline for 2-3 engineers)  
**Risk:** **Medium** — ML model training is data-dependent; if schedules data remains sparse (144 docs), delay prediction accuracy will be limited. Mitigation: start with synthetic delay injection for testing, then pivot to real schedules once AviationStack integration is proven.  
**Decisions I made for you:**
1. **Test framework: pytest** with `pytest-cov` — industry standard, integrates with CI
2. **ML stack: LightGBM** — lighter than XGBoost, faster training, good for TFM scope
3. **Model tracking: MLflow** — standard experiment tracking, integrates with model registry
4. **API: FastAPI** — async, auto-docs (Swagger), standard for inference endpoints
5. **Execution order: Tests → Data Quality → ML → API** — lock before building upward

Your next move: **Approve this plan** (or request a high-accuracy review). Once approved, execution starts with `$start-work`.

---

> TL;DR (machine): XL effort, medium risk, 4 parallel waves spanning test coverage (pytest), data quality (pydantic schemas + validation), ML training (LightGBM + MLflow), inference API (FastAPI), prediction archival (PostgreSQL), and enterprise visualization (Grafana). Schedule data via teammate's web scraping. Closes 100% of functional requirement gaps (RF-F01 through RF-F08) + non-functional constraints.

## Scope

### Must have
1. **Comprehensive pytest suite** (70+ tests) covering:
   - Data pipeline integration (bronze_to_silver, silver_to_gold_entities, build_feature_store)
   - Data quality (deduplication, normalization, null handling)
   - Feature store schema compliance
2. **Trained LightGBM delay prediction model** with:
   - MLflow experiment tracking (hyperparameter sweep, cross-validation results)
   - Model registry entry
   - Test set evaluation (RMSE, MAE, R²)
3. **FastAPI inference endpoint** exposing:
   - POST `/predict/delay` — returns delay prediction + confidence
   - POST `/predict/eta` — returns estimated arrival time
   - GET `/model/metadata` — returns model version + training timestamp
4. **Prediction archival & evaluation pipeline** (RF-F07 compliance):
   - Stores every prediction to `gold.predictions` table (request_id, model_version, features, predictions, timestamp)
   - Scheduled reconciliation job: join predictions with actual arrivals, compute MAE/drift, store evaluation report
5. **Minimal Streamlit dashboard** (RF-F06 compliance):
   - Display: delay distribution, top features by importance, model performance metrics, prediction history
   - Connect to PostgreSQL for live data
6. **CI/CD integration:**
   - pytest runs on every PR (blocks merge if < 70% coverage)
   - Model training checkpoint in pipeline (retrains on schedule)
   - Inference API health checks
   - Prediction evaluation job runs weekly

### Must NOT have (guardrails, anti-slop, scope boundaries)
- **NO Airflow/Kubernetes**: GitHub Actions cron is sufficient; add orchestration in Phase 2
- **NO Grafana/complex BI**: Simple Streamlit dashboard only (satisfies RF-F06, minimal tech debt)
- **NO production deployment**: API runs on localhost/dev servers only; no auto-deploy to production
- **NO data pipeline refactoring**: Use existing scripts as-is; only add tests/validation wrappers
- **NO schedule data bootstrap**: Relies on AviationStack; if sparse, use synthetic delay injection for testing
- **NO API authentication**: Public endpoint for MVP (add OAuth2 in Phase 2)
- **NO advanced model interpretability**: Use MLflow feature importance only; SHAP/LIME deferred to Phase 2

---

## Verification strategy
**Zero human intervention — all verification is agent-executed.**

- **Test decision:** TDD + pytest (write tests first, then implementation)
- **Data quality:** pydantic schemas + integration tests (validate every transform step)
- **ML:** 5-fold cross-validation + holdout test set (track with MLflow)
- **API:** pytest + integration tests (test with real PostgreSQL + mock OpenSky data)
- **Evidence:** `.omo/evidence/task-<N>-aeropredict-gap-closure.{log,json,png}` — pytest coverage reports, MLflow run history, API response samples

---

## Execution strategy

### Parallel execution waves

**Wave 1 (Week 1, 6 todos):** Test Infrastructure
- Set up pytest, write data pipeline tests, lock expectations

**Wave 2 (Week 1-2, 5 todos):** Data Quality & Feature Store
- Pydantic schemas, validation wrappers, feature store completeness tests

**Wave 3 (Week 2-3, 8 todos):** ML Model Training & Tracking  
- Dataset preparation, feature engineering, hyperparameter search, model registry

**Wave 4 (Week 3-4, 7 todos):** API, Prediction Archival & Visualization
- FastAPI endpoint, prediction archival, Streamlit dashboard, integration tests, CI/CD pipeline update, health checks

**Final Verification (Day 28):** All waves complete → cross-functional QA pass

### Dependency matrix

| Wave | Todo | Depends on | Blocks | Parallel within |
|------|------|-----------|--------|-----------------|
| **1** | 1.1 pytest setup | — | 1.2-1.6, all future | 1.2-1.3 |
| **1** | 1.2 extract→bronze tests | 1.1 | 2.x | 1.1, 1.3-1.6 |
| **1** | 1.3 bronze→silver tests | 1.1 | 2.x | 1.1-1.2, 1.4-1.6 |
| **1** | 1.4 silver→gold tests | 1.1 | 2.x | 1.1-1.3, 1.5-1.6 |
| **1** | 1.5 feature_store tests | 1.1 | 3.x | 1.1-1.4, 1.6 |
| **1** | 1.6 schema definitions | 1.1 | 2.x | 1.1-1.5 |
| **2** | 2.1 pydantic models | 1.6 | 2.2-2.5 | 2.2-2.5 |
| **2** | 2.2 validation wrappers | 2.1 | 3.x | 2.1, 2.3-2.5 |
| **2** | 2.3 data quality tests | 2.1 | 3.x | 2.1-2.2, 2.4-2.5 |
| **2** | 2.4 feature completeness | 1.5 | 3.x | 2.1-2.3, 2.5 |
| **2** | 2.5 coverage report (70%+) | 1.x, 2.1-2.4 | 3.x | 2.1-2.4 |
| **3** | 3.1 dataset export (SQL) | 1.x, 2.x | 3.2 | 3.2-3.8 |
| **3** | 3.2 feature engineering | 3.1 | 3.3 | 3.1, 3.3-3.8 |
| **3** | 3.3 target construction | 3.2 | 3.4-3.8 | 3.2, 3.4-3.8 |
| **3** | 3.4 train/val/test split | 3.3 | 3.5-3.8 | 3.2-3.3, 3.5-3.8 |
| **3** | 3.5 baseline model | 3.4 | 3.6-3.8 | 3.2-3.4, 3.6-3.8 |
| **3** | 3.6 hyperparameter sweep | 3.5 | 3.7-3.8 | 3.2-3.5, 3.7-3.8 |
| **3** | 3.7 MLflow tracking | 3.5-3.6 | 3.8, 4.x | 3.2-3.6, 3.8 |
| **3** | 3.8 model registry | 3.7 | 4.x | 3.2-3.7 |
| **4** | 4.1 FastAPI scaffold | 3.8 | 4.2-4.7 | 4.2-4.7 |
| **4** | 4.2 predict/delay endpoint | 4.1 | 4.3-4.7 | 4.1, 4.3-4.7 |
| **4** | 4.3 predict/eta endpoint | 4.1 | 4.4-4.7 | 4.1-4.2, 4.4-4.7 |
| **4** | 4.4 integration tests | 4.1-4.3 | 4.5-4.7 | 4.1-4.3, 4.5-4.7 |
| **4** | 4.5 CI/CD + monitoring | 4.1-4.4, 3.8 | 4.6-4.7, Final | 4.1-4.4 |
| **4** | 4.6 prediction archival | 4.2-4.3 | 4.7, Final | 4.1-4.5, 4.7 |
| **4** | 4.7 Streamlit dashboard | 4.6, 3.8 | Final | 4.1-4.6 |
| **F** | F1-F4 verification wave | All 4.x | — | F1-F4 (parallel) |

---

## Todos

### **WAVE 1: Test Infrastructure (6 todos)**

- [x] **1.1 Set up pytest, coverage, and fixtures**
  - **What to do:** Create `tests/conftest.py` with reusable fixtures (Docker MongoDB/PostgreSQL, mock OpenSky data), configure pytest in `pyproject.toml` with coverage thresholds (70% minimum), add `tests/` directory structure.
  - **Must NOT do:** Do NOT modify existing pipeline scripts; only wrap them in test harnesses.
  - **Parallelization:** Wave 1 | Blocked by: — | Blocks: 1.2-1.6
  - **References:** `pyproject.toml:63` (pytest config), `src/aeropredict/opensky/models.py` (dataclasses to test), `.github/workflows/pipeline.yml` (CI trigger template)
  - **Acceptance criteria (agent-executable):** 
    - `pytest --collect-only tests/` returns 5+ test modules
    - `pytest --cov=src/aeropredict --cov-report=term-missing` shows 0% coverage (baseline before writing tests)
    - `conftest.py` provides `mongo_client`, `postgres_client`, `mock_opensky_data` fixtures
  - **QA scenarios:** 
    - Happy: Run `pytest tests/conftest.py::test_fixtures_available` → fixture injection works
    - Failure: Run `pytest tests/conftest.py::test_mongo_connection` → fails gracefully if MongoDB not running; test checks for error message
  - **Evidence:** `.omo/evidence/task-1-pytest-setup.log` (pytest output), `.omo/evidence/task-1-conftest.py` (fixture defs)
  - **Commit:** Y | `test(setup): initialize pytest with 70% coverage target and reusable fixtures`

- [x] **1.2 Write tests for extract_to_bronze.py (Bronze layer)**
  - **What to do:** Write 15+ tests covering API calls, checkpoint logic, Delta Lake writes, idempotency, rate-limit handling, and dual-write (local + R2). Mock OpenSky API responses; verify Delta Lake table schema and partition structure.
  - **Must NOT do:** Do NOT call real OpenSky API; always mock. Do NOT modify extraction logic; only test it.
  - **Parallelization:** Wave 1 | Blocked by: 1.1 | Blocks: 2.x
  - **References:** `scripts/extract_to_bronze.py:333` (full script), `src/aeropredict/opensky/extract_flights.py` (flight extraction), `src/aeropredict/opensky/storage.py:80-150` (Bronze Delta writes), `data/mock/opensky/` (sample data)
  - **Acceptance criteria:**
    - `pytest tests/test_extract_to_bronze.py -v` → all 15+ tests PASS
    - `pytest tests/test_extract_to_bronze.py --cov=src/aeropredict.opensky.extract_flights --cov-report=term` shows 85%+ coverage
    - Checkpoint idempotency: running the same extraction twice produces identical Delta Lake partition
  - **QA scenarios:**
    - Happy: Extract 2 days of mock data → Delta Lake `bronze/opensky` has 2 partitions with 100+ rows each
    - Failure: Mock API returns 429 → checkpoint advances but extraction stops gracefully; re-run succeeds
  - **Evidence:** `.omo/evidence/task-2-extract-tests.log`, `.omo/evidence/task-2-delta-schema.json` (table schema)
  - **Commit:** Y | `test(extract): add 15+ tests for OpenSky API ingestion and Bronze layer idempotency`

- [x] **1.3 Write tests for bronze_to_silver.py (Transform)**
  - **What to do:** Write 12+ tests covering MongoDB writes, date-range filtering, deduplication logic, null handling, and checkpoint. Verify schema compliance via pydantic (see Wave 2).
  - **Must NOT do:** Do NOT modify bronze_to_silver logic; only test it. Do NOT test date range queries with real DB; use fixtures.
  - **Parallelization:** Wave 1 | Blocked by: 1.1 | Blocks: 2.x
  - **References:** `scripts/bronze_to_silver.py:222`, `src/aeropredict/opensky/storage_silver.py` (MongoDB writes)
  - **Acceptance criteria:**
    - `pytest tests/test_bronze_to_silver.py -v` → all 12+ tests PASS
    - Deduplication: insert 10 duplicate flights → MongoDB has exactly 1 document (via upsert key)
    - Schema validation: every inserted document passes pydantic Flight model
  - **QA scenarios:**
    - Happy: Transform 1 day of Bronze data → MongoDB flights collection has N documents matching filter
    - Failure: MongoDB connection down → error is caught, logged, and test fails with clear message
  - **Evidence:** `.omo/evidence/task-3-silver-tests.log`
  - **Commit:** Y | `test(silver): add 12+ tests for Bronze→Silver transformation and MongoDB writes`

- [x] **1.4 Write tests for silver_to_gold.py and silver_to_gold_entities.py (PostgreSQL aggregations)**
  - **What to do:** Write 16+ tests covering:
    - `silver_to_gold.py`: daily_airport_traffic, route_density, hourly_distribution aggregations
    - `silver_to_gold_entities.py`: flights, aircraft, weather entity syncs; upsert logic; ON CONFLICT handling
  - **Must NOT do:** Do NOT modify aggregation logic. Do NOT query large production PostgreSQL; use test fixtures.
  - **Parallelization:** Wave 1 | Blocked by: 1.1 | Blocks: 2.x
  - **References:** `scripts/silver_to_gold.py:223`, `scripts/silver_to_gold_entities.py:203`, `src/aeropredict/opensky/storage_gold.py`
  - **Acceptance criteria:**
    - `pytest tests/test_silver_to_gold.py tests/test_entities.py -v` → all 16+ tests PASS
    - daily_airport_traffic: aggregate 20 flights at LEMD → 1 row with count=20, date matches first flight
    - Upsert idempotency: insert aircraft twice → exactly 1 row in gold.aircraft (no duplicates)
  - **QA scenarios:**
    - Happy: Sync 50 flights → gold.flights has 50 rows; gold.daily_airport_traffic has correct aggregations
    - Failure: PostgreSQL constraint violation (null in NOT NULL column) → test catches and reports
  - **Evidence:** `.omo/evidence/task-4-gold-tests.log`, `.omo/evidence/task-4-postgres-schema.sql`
  - **Commit:** Y | `test(gold): add 16+ tests for Silver→Gold aggregations and entity syncs with upsert logic`

- [x] **1.5 Write tests for build_feature_store.py (Feature engineering)**
  - **What to do:** Write 14+ tests covering:
    - Feature joins (flights + schedules + aircraft + weather + aggregations)
    - Feature derivation (hour_of_day, day_of_week, route_distance, traffic stats)
    - Null handling and imputation
    - Feature store table schema compliance
  - **Must NOT do:** Do NOT modify feature logic; only test it.
  - **Parallelization:** Wave 1 | Blocked by: 1.1 | Blocks: 3.x
  - **References:** `scripts/build_feature_store.py:346`, `docs/analisis_prediccion_retrasos.md:40-80` (feature roadmap)
  - **Acceptance criteria:**
    - `pytest tests/test_feature_store.py -v` → all 14+ tests PASS
    - Feature count: gold.feature_store has exactly N expected columns (hour_of_day, day_of_week, airline, route_distance, weather_*, delay_target, etc.)
    - Null validation: no critical feature column has >5% nulls (or test documents acceptable null strategy)
  - **QA scenarios:**
    - Happy: Build feature store from 50 flights + 10 weather rows → 50 feature rows, all 25+ columns populated (except allowable nulls)
    - Failure: Missing schedule for flight → feature row exists with imputed/null schedule fields; test verifies strategy
  - **Evidence:** `.omo/evidence/task-5-features.log`, `.omo/evidence/task-5-feature-schema.json`
  - **Commit:** Y | `test(features): add 14+ tests for feature engineering and null handling strategy`

- [x] **1.6 Define pydantic schema models for all data layers**
  - **What to do:** Create `src/aeropredict/schemas.py` with pydantic v2 models for:
    - Bronze layer: `OpenSkyFlight`, `StateVector`, `Track`
    - Silver layer: `FlightDocument`, `WeatherDocument`, `ScheduleDocument`
    - Gold layer: `Flight`, `Aircraft`, `Weather`, `DailyAirportTraffic`, `RouteDensity`, `HourlyDistribution`
    - Feature store: `FeatureStoreRow`
    - Include validation rules (non-negative distances, valid airport codes, etc.)
  - **Must NOT do:** Do NOT change existing database inserts; only define schemas for validation/tests.
  - **Parallelization:** Wave 1 | Blocked by: 1.1 | Blocks: 2.1
  - **References:** `src/aeropredict/opensky/models.py` (existing dataclasses — convert to pydantic), `docs/mongo_schema.md` (field types/counts)
  - **Acceptance criteria:**
    - `python -c "from src.aeropredict.schemas import *; print('All schemas importable')"` → success
    - Pydantic validation: `FlightDocument(icao24="invalid", ...)` → raises ValidationError with clear message
    - Serialization: `Flight(...).model_dump_json()` → valid JSON
  - **QA scenarios:**
    - Happy: Load real MongoDB flight doc → parses as FlightDocument without error
    - Failure: Flight with null required field → ValidationError caught and logged
  - **Evidence:** `.omo/evidence/task-6-schemas.py` (schema definitions), `.omo/evidence/task-6-validation.log`
  - **Commit:** Y | `schema: define pydantic v2 models for all data layers with validation rules`

---

### **WAVE 2: Data Quality & Feature Store (5 todos)**

- [x] **2.1 Implement pydantic validation wrappers for pipeline scripts**
  - **What to do:** Create validation wrapper modules:
    - `src/aeropredict/validators.py`: Validate dataframes against pydantic schemas before writing to MongoDB/PostgreSQL
    - Hook into `bronze_to_silver.py`, `silver_to_gold*.py`, `build_feature_store.py`
    - Log validation errors and counts (e.g., "5 invalid rows rejected out of 1000")
  - **Must NOT do:** Do NOT block pipeline on validation errors (log and continue, for now); do NOT modify core scripts.
  - **Parallelization:** Wave 2 | Blocked by: 1.6 | Blocks: 2.2-2.5
  - **References:** `src/aeropredict/schemas.py` (pydantic models from 1.6), `scripts/bronze_to_silver.py:100-150` (MongoDB insert point)
  - **Acceptance criteria:**
    - `pytest tests/test_validators.py -v` → all validators work correctly
    - Validators reject 5% invalid data from test set, log rejection count
    - Pipeline continues after validation warnings (non-blocking)
  - **QA scenarios:**
    - Happy: 100 flights pass validation → 100 inserted to MongoDB
    - Failure: 5 flights have null callsign (invalid) → logged, 95 inserted, test verifies rejection count
  - **Evidence:** `.omo/evidence/task-7-validators.py`, `.omo/evidence/task-7-validation-warnings.log`
  - **Commit:** Y | `feat(validation): add pydantic-based data quality checks at pipeline write points`

- [x] **2.2 Write data quality test suite (deduplication, normalization, nulls)**
  - **What to do:** Write 18+ tests covering:
    - Deduplication: same flight ingested twice → only 1 row in MongoDB/PostgreSQL
    - Normalization: airport codes uppercase, timestamps in UTC, distances positive
    - Null handling: document the strategy (drop vs impute) for each feature
    - Completeness: at least 80% of rows have non-null critical columns
  - **Must NOT do:** Do NOT modify pipeline logic; only verify expected behavior.
  - **Parallelization:** Wave 2 | Blocked by: 2.1 | Blocks: 3.x
  - **References:** `tests/test_validators.py` (reuse validation fixtures), `docs/analisis_prediccion_retrasos.md` (feature list)
  - **Acceptance criteria:**
    - `pytest tests/test_data_quality.py -v` → all 18+ tests PASS
    - Dedup test: 100 identical flight records → exactly 1 in database
    - Normalization: 100 flights with mixed-case airport codes → all uppercase in database
    - Null report: summary of nulls per column, passes if <5% for critical features
  - **QA scenarios:**
    - Happy: Ingest 1000 flights, run QA checks → all pass, nulls report shows <5% across critical columns
    - Failure: Normalization fails (lowercase airport code saved) → test catches and fails
  - **Evidence:** `.omo/evidence/task-8-dq-tests.log`, `.omo/evidence/task-8-null-report.json`
  - **Commit:** Y | `test(data-quality): add 18+ tests for deduplication, normalization, and completeness`

- [x] **2.3 Write feature store completeness tests**
  - **What to do:** Write 10+ tests verifying:
    - All expected columns present in gold.feature_store (hour_of_day, day_of_week, month, airline, route_distance, previous_delay, traffic_stats, weather_*, target_delay)
    - Row count: feature_store rows ≥ flights rows (should be 1:1, with some nulls for missing schedules)
    - Feature derivations: hour_of_day ∈ [0,23], day_of_week ∈ [0,6], distances > 0, etc.
    - Join correctness: all flights have matching weather/schedule rows (or expected nulls documented)
  - **Must NOT do:** Do NOT modify feature generation; only verify.
  - **Parallelization:** Wave 2 | Blocked by: 1.5, 2.1 | Blocks: 3.x
  - **References:** `scripts/build_feature_store.py:346`, `docs/analisis_prediccion_retrasos.md:80-120`
  - **Acceptance criteria:**
    - `pytest tests/test_feature_completeness.py -v` → all 10+ tests PASS
    - Column count: `SELECT * FROM gold.feature_store LIMIT 1` returns 25+ columns
    - Feature validation: hour_of_day ∈ [0,23] for all 100% of rows, day_of_week ∈ [0,6] for 100%
    - Join quality: 95%+ of flights have non-null schedule data (if sparse, document null strategy)
  - **QA scenarios:**
    - Happy: 50 flights with complete schedules → 50 feature rows, all columns populated
    - Failure: Missing column (e.g., route_distance) → test catches and fails
  - **Evidence:** `.omo/evidence/task-9-features-complete.log`, `.omo/evidence/task-9-feature-dist.json`
  - **Commit:** Y | `test(features): add 10+ tests for feature store schema and join correctness`

- [x] **2.4 Add pytest coverage validation and CI/CD gate (70% minimum)**
  - **What to do:**
    - Run `pytest --cov=src/aeropredict --cov-report=html tests/` locally to measure coverage
    - Update `.github/workflows/pipeline.yml` to run coverage check on every push to `main` and PR (block merge if <70%)
    - Add `pyproject.toml` configuration for pytest and coverage (already exists; update thresholds)
  - **Must NOT do:** Do NOT lower coverage threshold to pass CI artificially.
  - **Parallelization:** Wave 2 | Blocked by: 1.2-1.5, 2.1-2.3 | Blocks: 4.x
  - **References:** `pyproject.toml` (pytest config section), `.github/workflows/pipeline.yml:40-60` (add pytest job)
  - **Acceptance criteria:**
    - `pytest --cov=src/aeropredict --cov-report=term-missing` shows ≥70% overall coverage
    - CI check: PR without ≥70% coverage is blocked (GitHub Actions status check fails)
    - `.github/workflows/pipeline.yml` includes pytest + coverage job
  - **QA scenarios:**
    - Happy: Coverage = 72% → CI check passes, PR mergeable
    - Failure: Coverage = 65% → CI check fails with message "Coverage below 70% threshold"
  - **Evidence:** `.omo/evidence/task-10-coverage.html` (HTML report), `.omo/evidence/task-10-ci-check.log`
  - **Commit:** Y | `ci(coverage): add 70% coverage gate to PR merge checks and update pytest config`

- [x] **2.5 Document data quality strategy and feature engineering roadmap**
  - **What to do:** Update `docs/analisis_prediccion_retrasos.md` with:
    - Section "Data Quality Strategy": deduplication rules, null handling per column, validation rules
    - Section "Feature Engineering Roadmap": list all current features (hour_of_day, etc.) with derivation logic, identify future features (e.g., airport congestion index)
    - Section "Testing Strategy": how data tests validate each step, coverage targets, acceptance thresholds
  - **Must NOT do:** Do NOT make architectural changes; only document decisions already made.
  - **Parallelization:** Wave 2 | Blocked by: 2.1-2.4 | Blocks: 3.x
  - **References:** `docs/analisis_prediccion_retrasos.md` (current doc), `tests/test_validators.py`, `tests/test_data_quality.py`
  - **Acceptance criteria:**
    - Document updated with ≥3 sections, each with concrete examples
    - Data quality strategy covers all major transform steps (extract, dedup, normalize)
    - Feature roadmap lists 15+ current + 5+ future features with one-line derivations
  - **QA scenarios:**
    - Happy: Document is readable, links to test files, someone new can understand strategy
    - Failure: Document is incomplete or contradicts actual test logic
  - **Evidence:** `.omo/evidence/task-11-data-strategy.md`
  - **Commit:** Y | `docs: document data quality strategy and feature engineering roadmap`

---

### **WAVE 3: ML Model Training & Tracking (8 todos)**

- [x] **3.1 Export feature store data for ML (CSV + Parquet + SQL queries)**
  - **What to do:**
    - Query PostgreSQL `gold.feature_store` and export to `data/processed/feature_store.parquet` (for fast loading in ML)
    - Also save CSV for exploratory analysis: `data/processed/feature_store.csv`
    - Include data export script: `scripts/export_ml_dataset.py` with date range filtering and null removal options
    - **NOTE:** Schedule data is being populated by teammate via web scraping (separate task); assume `gold.schedules` table exists with flight_id, scheduled_arrival, scheduled_departure
    - Save metadata: row count, feature count, date range, null statistics → `data/processed/dataset_metadata.json`
  - **Must NOT do:** Do NOT modify feature store schema; only read and export. Do NOT implement schedule scraping (teammate handles).
  - **Parallelization:** Wave 3 | Blocked by: 2.5 | Blocks: 3.2
  - **References:** `scripts/build_feature_store.py` (feature schema), `docs/analisis_prediccion_retrasos.md` (features list)
  - **Acceptance criteria:**
    - `python scripts/export_ml_dataset.py --output data/processed/feature_store.parquet` succeeds
    - Parquet file exists, readable via pandas: `df = pd.read_parquet(...)`; shape is N rows × 25+ columns
    - Metadata JSON contains row count, feature count, null percentages
    - Schedule data available: assume `gold.schedules` has been populated by teammate's web scraping
  - **QA scenarios:**
    - Happy: Export 500 flights with complete features (including teammate-provided schedules) → Parquet file with 500 rows, 26 columns
    - Failure: Database connection down → error caught and reported
  - **Evidence:** `.omo/evidence/task-12-export.log`, `.omo/evidence/task-12-dataset-metadata.json`
  - **Commit:** Y | `script(ml): add dataset export script for ML training (assumes schedule data from teammate's web scraping)`

- [x] **3.2 Feature engineering and selection for delay prediction model**
  - **What to do:**
    - Load feature store dataset (from 3.1)
    - Perform exploratory analysis: correlation matrix, feature importance (tree-based), distribution plots
    - Select top 15-20 features based on correlation with delay target and domain knowledge
    - Create feature engineering notebook: `notebooks/01_feature_analysis.ipynb`
    - Document selected features: `docs/ml_feature_selection.md`
  - **Must NOT do:** Do NOT train a model yet; only analyze and select features.
  - **Parallelization:** Wave 3 | Blocked by: 3.1 | Blocks: 3.3-3.4
  - **References:** `data/processed/feature_store.parquet`, `docs/analisis_prediccion_retrasos.md`
  - **Acceptance criteria:**
    - Notebook runs end-to-end: `jupyter nbconvert --to script 01_feature_analysis.ipynb`
    - Output: 15-20 features selected, justification in notebook cells
    - Feature list exported to `data/processed/selected_features.json`
  - **QA scenarios:**
    - Happy: Correlation analysis identifies hour_of_day, route_distance, traffic_stats as top features → selected
    - Failure: No features correlated with target → document this finding, still proceed with all features
  - **Evidence:** `.omo/evidence/task-13-feature-analysis.ipynb`, `.omo/evidence/task-13-selected-features.json`
  - **Commit:** Y | `notebook(ml): exploratory feature analysis and selection for delay prediction`

- [x] **3.3 Construct target variable (delay = actual_arrival - scheduled_arrival)**
  - **What to do:**
    - Load feature store + schedule data from PostgreSQL
    - Compute delay_target: actual_arrival - scheduled_arrival (in minutes)
    - Handle edge cases: missing schedules (drop or flag), cancelled flights (handle per domain rule)
    - Visualize delay distribution (histogram): save to `reports/figures/delay_distribution.png`
    - Document target construction logic: `docs/ml_target_definition.md`
  - **Must NOT do:** Do NOT artificially inflate delay variance; only use real data.
  - **Parallelization:** Wave 3 | Blocked by: 3.1 | Blocks: 3.4-3.5
  - **References:** `docs/analisis_prediccion_retrasos.md:60` (target definition proposal), `scripts/collect_schedules.py` (schedule data collection)
  - **Acceptance criteria:**
    - Target computed: `delay_target = actual_arrival - scheduled_arrival` (in minutes)
    - Missing schedules: document handling (drop rows / impute / flag)
    - Distribution visualization saved: histogram showing delay range, mean, std
  - **QA scenarios:**
    - Happy: 400 flights with schedules → 400 delay values computed; distribution shows mean ≈ 5min, std ≈ 15min (realistic)
    - Failure: 100 flights missing schedules → drop or flag; 300 flights remain for training
  - **Evidence:** `.omo/evidence/task-14-target-dist.png`, `.omo/evidence/task-14-target-definition.md`
  - **Commit:** Y | `feat(ml): construct delay target variable and document handling strategy`

- [x] **3.4 Split data into train/val/test sets (60/20/20) with temporal stratification**
  - **What to do:**
    - Load feature store + target variable (from 3.3)
    - Stratify by date: earlier 60% → train, middle 20% → validation, latest 20% → test (preserves temporal order for realistic evaluation)
    - Save splits to parquet files: `data/processed/train_set.parquet`, `val_set.parquet`, `test_set.parquet`
    - Document split logic and statistics: `docs/ml_data_split.md` (row counts, date ranges, target distribution per split)
  - **Must NOT do:** Do NOT random shuffle (breaks temporal order); do NOT leak test data.
  - **Parallelization:** Wave 3 | Blocked by: 3.3 | Blocks: 3.5-3.8
  - **References:** `scripts/export_ml_dataset.py` (dataset export)
  - **Acceptance criteria:**
    - `len(train_set) ≈ 0.6 * N`, `len(val_set) ≈ 0.2 * N`, `len(test_set) ≈ 0.2 * N`
    - Date ranges: train dates < val dates < test dates (temporal order preserved)
    - Target statistics: mean/std similar across all splits (no distribution shift)
  - **QA scenarios:**
    - Happy: 500 flights split into 300 train / 100 val / 100 test; dates ordered ✓
    - Failure: Random shuffle causes test dates < train dates → fails temporal check
  - **Evidence:** `.omo/evidence/task-15-split-stats.json` (row counts, date ranges), `.omo/evidence/task-15-target-dist-per-split.png`
  - **Commit:** Y | `feat(ml): split dataset into train/val/test with temporal stratification`

- [x] **3.5 Train baseline LightGBM model and evaluate on test set**
  - **What to do:**
    - Load train/val/test sets (from 3.4)
    - Train LightGBM with default hyperparameters: `LGBMRegressor(n_estimators=100, max_depth=7, learning_rate=0.05)`
    - Evaluate on test set: compute RMSE, MAE, R² scores
    - Create baseline evaluation report: `reports/baseline_model_metrics.json`
    - Save baseline model: `models/baseline_lgb.pkl` (joblib)
  - **Must NOT do:** Do NOT tune hyperparameters yet; this is baseline only.
  - **Parallelization:** Wave 3 | Blocked by: 3.4 | Blocks: 3.6-3.8
  - **References:** LightGBM docs, `docs/analisis_prediccion_retrasos.md:130` (ML stack)
  - **Acceptance criteria:**
    - Model trains: `LGBMRegressor(...).fit(X_train, y_train)` succeeds
    - Test evaluation: RMSE, MAE, R² computed and saved to JSON
    - Baseline metrics: RMSE < 30 min (acceptable for delays), R² > 0.3 (weak but present)
    - Model pickled: `models/baseline_lgb.pkl` exists and is loadable
  - **QA scenarios:**
    - Happy: Train/test on 300/100 flights → model converges, RMSE ≈ 20min, R² ≈ 0.4
    - Failure: RMSE >> 100min, R² < 0 → investigate feature quality, document finding
  - **Evidence:** `.omo/evidence/task-16-baseline-metrics.json`, `.omo/evidence/task-16-baseline-importance.png` (feature importances)
  - **Commit:** Y | `ml(baseline): train baseline LightGBM model and evaluate on test set`

- [x] **3.6 Hyperparameter sweep with MLflow tracking (grid search or Optuna)**
  - **What to do:**
    - Load train/val/test sets
    - Define hyperparameter grid: `n_estimators ∈ [50, 100, 200]`, `max_depth ∈ [5, 7, 10]`, `learning_rate ∈ [0.01, 0.05, 0.1]`
    - Use Optuna or GridSearchCV to search: 27 trials (3×3×3)
    - Track all trials with MLflow: `mlflow.log_params()`, `mlflow.log_metrics()` for each trial
    - Best model: save to MLflow Model Registry
  - **Must NOT do:** Do NOT run exhaustive search (keep to ~30 trials); do NOT tune on test set.
  - **Parallelization:** Wave 3 | Blocked by: 3.5 | Blocks: 3.7-3.8
  - **References:** Optuna docs, MLflow docs, `docs/analisis_prediccion_retrasos.md`
  - **Acceptance criteria:**
    - Hyperparameter sweep completes: 27 trials with MLflow tracking
    - Best trial has validation metrics (RMSE, MAE, R²) logged to MLflow
    - Best model RMSE < 25 min, R² > 0.35 (improvement over baseline)
    - MLflow UI accessible: `mlflow ui` → shows all trials, best run highlighted
  - **QA scenarios:**
    - Happy: 27 trials complete; best trial RMSE = 18min → improvement over baseline
    - Failure: All trials perform worse than baseline → investigate features/data quality
  - **Evidence:** `.omo/evidence/task-17-mlflow-runs.json` (all trial metrics), `.omo/evidence/task-17-best-params.json`
  - **Commit:** Y | `ml(hpo): hyperparameter sweep with MLflow tracking and best model registration`

- [x] **3.7 Register best model in MLflow Model Registry with version tagging**
  - **What to do:**
    - Promote best model from hyperparameter sweep to MLflow Model Registry: `mlflow.register_model(..., "delay-predictor")`
    - Set model version: `version = "1.0.0"`, timestamp of training
    - Log model metadata: feature list, training dataset size, validation metrics
    - Create model card: `docs/model_card_delay_predictor.md` with description, performance, features, limitations
  - **Must NOT do:** Do NOT register a model without documented performance metrics.
  - **Parallelization:** Wave 3 | Blocked by: 3.6 | Blocks: 3.8, 4.x
  - **References:** MLflow Model Registry docs, best model from 3.6
  - **Acceptance criteria:**
    - Model registered in MLflow: `mlflow models list()` includes `delay-predictor/1.0.0`
    - Metadata logged: feature count, training metrics, model type (LightGBM)
    - Model card created: `docs/model_card_delay_predictor.md` (≥500 words, includes perf metrics)
    - Model loadable: `mlflow.pyfunc.load_model(...)` succeeds
  - **QA scenarios:**
    - Happy: Model registered, card complete, can load via pyfunc API
    - Failure: Missing metrics or card → fails acceptance
  - **Evidence:** `.omo/evidence/task-18-model-card.md`, `.omo/evidence/task-18-mlflow-registry.log`
  - **Commit:** Y | `ml(registry): register delay-predictor model with metadata and model card`

- [x] **3.8 Add ML model training to CI/CD pipeline (periodic retraining on schedule)**
  - **What to do:**
    - Create `.github/workflows/model-training.yml`: schedule to run weekly (e.g., Sunday 00:00 UTC)
    - Workflow steps: export dataset → feature selection → train/hyperparameter sweep → register model
    - On success: register new model version; on failure: send alert (Slack/email)
    - Update main `pipeline.yml` to reference model version in feature store step
  - **Must NOT do:** Do NOT train model on every pipeline run (too slow); use weekly schedule.
  - **Parallelization:** Wave 3 | Blocked by: 3.7 | Blocks: 4.x
  - **References:** `.github/workflows/pipeline.yml` (template), 3.1-3.7 scripts
  - **Acceptance criteria:**
    - Workflow file created: `.github/workflows/model-training.yml` with weekly schedule
    - Workflow runs successfully once (manual trigger): data export → training → registration
    - New model version appears in MLflow registry
  - **QA scenarios:**
    - Happy: Workflow triggers on schedule, trains new model, registers to MLflow
    - Failure: Training fails → error logged; main pipeline continues (non-blocking)
  - **Evidence:** `.omo/evidence/task-19-training-workflow.log`, `.omo/evidence/task-19-new-model-version.json`
  - **Commit:** Y | `ci(ml): add weekly model retraining workflow to GitHub Actions`

---

### **WAVE 4: API & Monitoring (5 todos)**

- [x] **4.1 Scaffold FastAPI inference server with model loading**
  - **What to do:**
    - Create `src/aeropredict/api/server.py`: FastAPI app with model loading from MLflow registry
    - Load model at startup: `model = mlflow.pyfunc.load_model(f"models:/delay-predictor/production")`
    - Health check endpoint: GET `/health` → returns `{"status": "ok", "model_version": "1.0.0"}`
    - Add logging: all requests logged with timestamp, input features, prediction time
    - Create `src/aeropredict/api/models.py`: pydantic request/response models for API inputs/outputs
  - **Must NOT do:** Do NOT add authentication yet; public endpoint for MVP.
  - **Parallelization:** Wave 4 | Blocked by: 3.8 | Blocks: 4.2-4.5
  - **References:** FastAPI docs, MLflow pyfunc docs, `src/aeropredict/schemas.py` (data models)
  - **Acceptance criteria:**
    - FastAPI app starts: `uvicorn src.aeropredict.api.server:app --reload` runs on localhost:8000
    - Health check works: `GET http://localhost:8000/health` → 200 OK, JSON response
    - Model loads: startup logs show "Model loaded: delay-predictor/1.0.0"
  - **QA scenarios:**
    - Happy: Server starts, health check returns status="ok"
    - Failure: Model not found in MLflow → error caught, health check returns 503
  - **Evidence:** `.omo/evidence/task-20-server-startup.log`
  - **Commit:** Y | `api(core): scaffold FastAPI server with model loading and health check`

- [x] **4.2 Implement POST /predict/delay endpoint**
  - **What to do:**
    - Create POST `/predict/delay` endpoint accepting flight features (JSON):
      ```json
      {
        "hour_of_day": 14,
        "day_of_week": 2,
        "airline": "IB",
        "route_distance": 500,
        "weather_wind_speed": 10,
        ...
      }
      ```
    - Validate input via pydantic model: `DelayPredictionRequest`
    - Run model inference: `prediction = model.predict([features])`
    - Return response: `DelayPredictionResponse` with `predicted_delay_minutes`, `confidence` (std dev from ensemble), `model_version`
    - Log all predictions: timestamp, input, output, inference time (milliseconds)
  - **Must NOT do:** Do NOT cache predictions (always inference fresh); do NOT expose model internals.
  - **Parallelization:** Wave 4 | Blocked by: 4.1 | Blocks: 4.4-4.5
  - **References:** FastAPI request/response validation, `src/aeropredict/schemas.py`, MLflow pyfunc inference
  - **Acceptance criteria:**
    - Endpoint accepts POST with valid flight features
    - Response includes predicted_delay_minutes, confidence, model_version
    - Invalid input (missing required field) returns 422 with error message
    - Inference time < 100ms for single prediction
  - **QA scenarios:**
    - Happy: POST with valid features → delay prediction ∈ [-30, +120] minutes, confidence > 0
    - Failure: Missing required field → 422 Unprocessable Entity with clear error
  - **Evidence:** `.omo/evidence/task-21-delay-api.log`, `.omo/evidence/task-21-api-requests.json` (sample requests/responses)
  - **Commit:** Y | `api(endpoints): implement POST /predict/delay endpoint with validation and logging`

- [x] **4.3 Implement POST /predict/eta endpoint**
  - **What to do:**
    - Create POST `/predict/eta` endpoint accepting flight features + scheduled_arrival:
      ```json
      {
        "scheduled_arrival": "2026-06-21T18:30:00Z",
        "hour_of_day": 14,
        ...
      }
      ```
    - Compute ETA: `eta = scheduled_arrival + predicted_delay` (from 4.2 model)
    - Return response: `ETAPredictionResponse` with `estimated_arrival_time`, `confidence`, `delay_component`
    - Handle edge case: if delay > 240min, flag as "likely disruption" in response
  - **Must NOT do:** Do NOT override user-provided scheduled_arrival; use as-is.
  - **Parallelization:** Wave 4 | Blocked by: 4.1, 4.2 | Blocks: 4.4-4.5
  - **References:** 4.2 endpoint, datetime handling in Python
  - **Acceptance criteria:**
    - Endpoint accepts POST with scheduled_arrival + features
    - Response includes estimated_arrival_time (ISO 8601), confidence, delay_component
    - Edge case: delay > 240min → flag "disruption_likely": true
  - **QA scenarios:**
    - Happy: scheduled_arrival 18:30 + predicted_delay +15min → ETA 18:45
    - Failure: Malformed timestamp → 422 error
  - **Evidence:** `.omo/evidence/task-22-eta-api.log`
  - **Commit:** Y | `api(endpoints): implement POST /predict/eta endpoint with disruption flagging`

- [x] **4.4 Write integration tests for FastAPI endpoints (happy path + error cases)**
  - **What to do:**
    - Create `tests/test_api_integration.py` with 12+ tests:
      - Health check: GET /health returns 200
      - /predict/delay: valid input → 200 with prediction; invalid input → 422
      - /predict/eta: valid input → 200 with ETA; missing scheduled_arrival → 422
      - Model loading: model version matches expected
      - Error handling: model not found → 503; invalid feature value → 422
    - Use pytest `TestClient` from FastAPI to run against live server
  - **Must NOT do:** Do NOT test model accuracy here (that's ML tests); only test API contract.
  - **Parallelization:** Wave 4 | Blocked by: 4.1-4.3 | Blocks: 4.5
  - **References:** FastAPI TestClient, pytest docs, `tests/conftest.py` (fixtures)
  - **Acceptance criteria:**
    - `pytest tests/test_api_integration.py -v` → all 12+ tests PASS
    - Coverage: /health, /predict/delay, /predict/eta all tested with happy + error paths
    - Response validation: all responses match expected schema (pydantic models)
  - **QA scenarios:**
    - Happy: Send valid payload → 200 OK with valid response
    - Failure: Send invalid payload (missing field) → 422 with error detail
  - **Evidence:** `.omo/evidence/task-23-api-tests.log`
  - **Commit:** Y | `test(api): add 12+ integration tests for FastAPI endpoints`

- [x] **4.5 Update CI/CD pipeline: add API health checks and model registry validation**
  - **What to do:**
    - Update `.github/workflows/pipeline.yml` to include final steps:
      - Verify model in MLflow registry (version exists, metrics logged)
      - Start API server in background, run health check: `curl -f http://localhost:8000/health`
      - Run sample inference: send test payload, verify response shape
      - On success: API deployment ready; on failure: alert (Slack/email)
    - Add model version to pipeline output: annotate logs with model version used
    - Add monitoring alert: if model metrics degrade >10% from baseline, send notification
  - **Must NOT do:** Do NOT deploy API to production; only verify local readiness.
  - **Parallelization:** Wave 4 | Blocked by: 4.1-4.4, 3.8 | Blocks: Final verification
  - **References:** `.github/workflows/pipeline.yml`, curl docs, GitHub Actions secrets for notifications
  - **Acceptance criteria:**
    - CI workflow includes model registry validation step
    - API health check runs and passes (200 OK)
    - Sample inference succeeds: request → response validates against schema
    - Workflow logs show model version and metrics
  - **QA scenarios:**
    - Happy: Pipeline completes; model registered, API health OK, inference works
    - Failure: Model missing from registry → CI fails with clear error message
   - **Evidence:** `.omo/evidence/task-24-ci-final.log`
   - **Commit:** Y | `ci(monitoring): add API health checks and model registry validation to pipeline`

- [x] **4.6 Implement prediction archival & evaluation pipeline (RF-F07 compliance)**
  - **What to do:**
    - Create PostgreSQL table `gold.predictions` with columns: request_id (UUID), model_version, flight_features (JSON), predicted_delay_minutes, predicted_eta, timestamp, actual_arrival (nullable initially)
    - Modify FastAPI endpoints (`/predict/delay`, `/predict/eta`) to log every prediction to `gold.predictions` before returning response
    - Create scheduled job script `scripts/evaluate_predictions.py`:
      - Query predictions where actual_arrival IS NULL but timestamp < now() - 24h (should have actual data)
      - Join with actual arrivals from `gold.flights` table (actual_arrival timestamp)
      - Compute MAE, RMSE, R² comparing predicted_delay vs (actual_arrival - scheduled_arrival)
      - Store evaluation report: `data/reports/evaluation_latest.json` with metrics, timestamp, sample predictions
    - Add job to GitHub Actions weekly schedule (e.g., Monday 06:00 UTC)
  - **Must NOT do:** Do NOT block API responses while archiving (async logging only); do NOT delete old predictions.
  - **Parallelization:** Wave 4 | Blocked by: 4.2-4.3 | Blocks: Final verification
  - **References:** FastAPI middleware for logging, PostgreSQL JSON, cron job scheduling, `scripts/build_feature_store.py` (actual_arrival schema)
  - **Acceptance criteria:**
    - Table `gold.predictions` created with required columns
    - FastAPI endpoints log predictions: sample call returns 200, prediction appears in `gold.predictions` within 1s
    - Evaluation script runs: `python scripts/evaluate_predictions.py` succeeds, writes report to `data/reports/evaluation_latest.json`
    - Evaluation report contains: MAE, RMSE, R², sample predictions (5-10 rows), timestamp
    - GitHub Actions workflow includes weekly evaluation job
  - **QA scenarios:**
    - Happy: 100 predictions logged over 24h, evaluation job joins with actual data, computes 4 metrics, report saved
    - Failure: Missing actual_arrival data → evaluation gracefully skips incomplete predictions, logs count
  - **Evidence:** `.omo/evidence/task-25-predictions-archival.log`, `.omo/evidence/task-25-eval-report.json`
  - **Commit:** Y | `feat(ml): implement prediction archival and weekly evaluation pipeline for RF-F07 compliance`

- [x] **4.7 Build Grafana dashboard for visualization (RF-F06 compliance)**
  - **What to do:**
    - Set up Grafana instance (Docker container, runs on localhost:3000)
    - Connect PostgreSQL as data source: `postgresql://aeropredict:aeropredict@localhost:5432/aeropredict`
    - Create 4 dashboards (JSON config version-controlled in `.omo/dashboards/`):
      1. **Model Performance**: 
         - Latest MAE/RMSE/R² metrics from `data/reports/evaluation_latest.json` (stat panels)
         - Confusion matrix heatmap: predicted vs actual delay categories (query `gold.predictions` join with actuals)
      2. **Feature Importance**: 
         - Bar chart of top 10 LightGBM features (import from MLflow model card as CSV, static panel)
      3. **Prediction History**: 
         - Table panel: last 50 predictions from `gold.predictions` (request_id, delay_pred, eta_pred, timestamp)
         - Date range filter (user-selectable)
      4. **Data Quality**: 
         - Gauge panels: null % per critical feature in `gold.feature_store`
         - Row counts per stage: bronze, silver, gold (using COUNT queries)
    - Enable auto-refresh: 5min intervals for live data updates
    - Export dashboard JSON for version control
  - **Must NOT do:** Do NOT add authentication (public for MVP); do NOT customize beyond 4 dashboards.
  - **Parallelization:** Wave 4 | Blocked by: 4.6, 3.8 (MLflow model card) | Blocks: Final verification
  - **References:** Grafana docs, PostgreSQL Grafana plugin, dashboard JSON format
  - **Acceptance criteria:**
    - `docker run -d -p 3000:3000 grafana/grafana` starts successfully
    - All 4 dashboards accessible on localhost:3000 without login
    - Panels query PostgreSQL live (SHOW queries in inspector)
    - Auto-refresh working: data updates every 5min
    - Dashboard JSON exported: `.omo/dashboards/aeropredict-grafana.json`
  - **QA scenarios:**
    - Happy: All panels load, queries return data, refresh works every 5min
    - Failure: PostgreSQL down → Grafana shows "no data" error gracefully
  - **Evidence:** `.omo/evidence/task-26-grafana.log`, `.omo/evidence/task-26-grafana-screenshot.png` (4 dashboard views)
  - **Commit:** Y | `feat(dashboard): add Grafana visualization dashboard for RF-F06 compliance and enterprise monitoring`

---

- [x] **F1. Plan Compliance Audit**
  - Verify: All 24 todos from Waves 1-4 committed and merged to `main`
  - Verify: `.omo/evidence/` contains all expected artifacts (logs, schemas, reports, models)
  - Verify: 70% test coverage achieved; CI/CD gates pass
  - Result: PASS/FAIL with evidence summary

- [x] **F2. Code Quality Review (read-only oracle)**
  - Verify: No `TODO`, `FIXME`, or `XXX` comments left behind (except documented technical debt)
  - Verify: All new modules follow ruff lint rules (E/F/I/W/N/UP/B/C4/RUF)
  - Verify: No dead code or unused imports
  - Result: PASS/FAIL with findings

- [x] **F3. Real Manual QA (hands-on)**
  - Run: `docker compose up -d && python scripts/mock_extract_to_bronze.py --days 1`
  - Verify: Pipeline succeeds (no errors in logs)
  - Run: `pytest tests/ --cov=src/aeropredict` — verify 70%+ coverage
  - Run: `uvicorn src.aeropredict.api.server:app` + test endpoints (health, /predict/delay, /predict/eta)
  - Result: PASS/FAIL with evidence screenshots/logs

- [x] **F4. Scope Fidelity Check**
  - Verify: All 14 RF-01 through RF-14 requirements (current) are PASSING
  - Verify: All 8 RF-F01 through RF-F08 requirements (future) are implemented and PASSING
  - Verify: All 10 RNF-01 through RNF-10 requirements (current) are PASSING
  - Result: Traceability matrix: requirement → todos → evidence

---

## Commit strategy

Each todo generates ONE commit with commit message:
- **Format:** `<type>(<scope>): <summary>` per Conventional Commits
- **Types:** `test`, `feat`, `docs`, `ci`, `ml`, `api`, `schema`, `notebook`
- **Scope:** function area (e.g., `extract`, `silver`, `gold`, `features`, `training`, `endpoints`, `monitoring`)
- **Summary:** 1 line, imperative mood, ≤50 chars

**Example commits (in Wave 1-4 order):**
```
test(setup): initialize pytest with 70% coverage target
test(extract): add 15+ tests for OpenSky API and Bronze idempotency
test(silver): add 12+ tests for Bronze→Silver transformation
test(gold): add 16+ tests for Silver→Gold aggregations
test(features): add 14+ tests for feature engineering
schema: define pydantic v2 models for all data layers
feat(validation): add pydantic-based data quality checks
test(data-quality): add 18+ tests for deduplication
test(features): add 10+ tests for feature store completeness
ci(coverage): add 70% coverage gate to PR merge checks
docs: document data quality strategy and feature roadmap
script(ml): add dataset export script for ML training
notebook(ml): exploratory feature analysis and selection
feat(ml): construct delay target variable
feat(ml): split dataset with temporal stratification
ml(baseline): train baseline LightGBM model
ml(hpo): hyperparameter sweep with MLflow tracking
ml(registry): register delay-predictor model with metadata
api(core): scaffold FastAPI server with model loading
api(endpoints): implement POST /predict/delay endpoint
api(endpoints): implement POST /predict/eta endpoint
test(api): add 12+ integration tests for FastAPI
ci(monitoring): add API health checks and model registry validation
feat(ml): implement prediction archival and weekly evaluation pipeline
feat(dashboard): add Streamlit visualization dashboard for monitoring
```

**Branch strategy:** Single feature branch `feature/gap-closure` for all 26 todos, merge to `main` once all pass Final Verification.

---

## Success criteria

**All of these must be TRUE for the plan to declare complete:**

1. ✅ Test Coverage: `pytest --cov=src/aeropredict` shows ≥70% overall coverage
2. ✅ Data Quality: All 18+ data quality tests PASS; no deduplication errors; nulls documented
3. ✅ ML Model: LightGBM model trained, test RMSE < 25 min, R² > 0.35, registered in MLflow
4. ✅ Prediction Archival: PostgreSQL `gold.predictions` table populated, weekly evaluation job runs, reports stored
5. ✅ Streamlit Dashboard: 4 pages (Model Performance, Features, Predictions, Data Quality) load without errors
6. ✅ API: FastAPI server runs, /health returns 200, /predict/delay and /predict/eta respond correctly
7. ✅ CI/CD: GitHub Actions pipeline includes pytest, coverage gate, model training, API health checks, prediction evaluation
8. ✅ Documentation: Updated `docs/analisis_prediccion_retrasos.md`, `docs/data_quality_strategy.md`, `docs/model_card_delay_predictor.md`
9. ✅ Commits: All 26 todos committed to feature branch, commit messages follow Conventional Commits
10. ✅ Scope Closure: Requirements matrix shows RF-01 through RF-F08, RNF-01 through RNF-10 all PASSING; RF-F06 (visualization) + RF-F07 (prediction archival) explicitly traced

**Timeline estimate:**
- Wave 1 (tests): 4-5 days (1 engineer)
- Wave 2 (data quality): 3-4 days (1 engineer, parallel with Wave 1 tail)
- Wave 3 (ML training): 6-8 days (1 engineer, some blocking on Wave 2)
- Wave 4 (API + prediction archival + dashboard): 5-6 days (1 engineer, parallel with Wave 3 tail)
- Final Verification: 1 day
- **Total: 3.5-4 weeks for team of 2-3, or 7-9 weeks solo**
