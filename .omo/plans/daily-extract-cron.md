# Daily Extract Cron — Script de extracción diaria OpenSky

## TL;DR

> **Quick Summary**: Crear un script Python (`daily_extract.py`) que se ejecute vía cron 2x/día para extraer automáticamente vuelos históricos del día anterior desde OpenSky API, verificando créditos disponibles y saltando aeropuertos ya cargados en Delta Lake.
>
> **Deliverables**:
> - `src/aeropredict/opensky/daily_extract.py` — Script principal
> - `src/aeropredict/opensky/logging_config.py` — Configuración logging (extraída de la lógica)
> - `src/aeropredict/opensky/credit_checker.py` — Módulo de verificación de créditos API
> - Crontab configurado para ejecución 2x/día
> - Archivo de log en `data/logs/daily_extract.log`
>
> **Estimated Effort**: Short
> **Parallel Execution**: YES — Wave 1 tiene 3 tareas paralelas
> **Critical Path**: T1 → T3 → T4 → T6 → T7

---

## Context

### Original Request
"Crear un script para ejecutar diariamente como cron job la extracción de datos históricos de OpenSky, que se ejecute cuando se resetee el rate limit. Prefiere cron fijo 2x/día (6:00 y 19:00 UTC)."

### Interview Summary
**Key Decisions**:
- **Cron fijo 2x/día** a las 6:00 y 19:00 UTC (no auto-reprogramación dinámica)
- **Script Python dedicado** en `src/aeropredict/opensky/daily_extract.py`
- **Verificación de créditos** mediante request HEAD/GET ligero, leyendo `X-Rate-Limit-Remaining`
- **Umbral mínimo**: 2000 créditos para proceder
- **Skip aeropuertos ya cargados** en Delta Lake para el día consultado
- **Delay 5s** entre peticiones para evitar rate limiting por minuto
- **Manejo silencioso** de 404 (sin datos en rango)
- **Logging** a `data/logs/daily_extract.log`

**Research Findings**:
- OpenSky API: 4000 créditos/día para `/flights/*`, 30 créditos/consulta (1-2 particiones)
- ~30 aeropuertos españoles × 2 queries (arr+dep) × 30 créditos ≈ 1800 créditos/día completo
- Ya existen ~22,800 vuelos en silver_flights (30-mayo a 4-junio)
- Falta completar 2026-06-05 y extraer 2026-06-06 en adelante

---

## Work Objectives

### Core Objective
Automatizar la extracción diaria de vuelos históricos OpenSky para mantener actualizado el dataset de entrenamiento del modelo de predicción de retrasos.

### Concrete Deliverables
- `src/aeropredict/opensky/daily_extract.py` — Script ejecutable por cron
- `data/logs/daily_extract.log` — Log rotado de ejecuciones
- Crontab entry instalada

### Definition of Done
- [ ] El script se ejecuta sin errores vía crontab 2x/día
- [ ] Extrae vuelos de D-1 (ayer) para todos los aeropuertos españoles con datos disponibles
- [ ] No duplica datos ya existentes en Delta (verifica antes de consultar)
- [ ] Verifica créditos antes de empezar y aborta si < 2000
- [ ] Logging completo con timestamp, aeropuerto, vuelos extraídos, errores

### Must Have
- Verificación de créditos API antes de extraer
- Skip aeropuertos ya en Delta Lake
- Delay 5s entre peticiones
- Ejecutable desde crontab sin interacción
- Logging a archivo con rotación

### Must NOT Have (Guardrails)
- No modificar el pipeline existente (client, storage, extract_flights)
- No enviar notificaciones (emails, Slack, etc.)
- No implementar UI ni dashboard
- No tocar el entry point CLI existente en pyproject.toml

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES (pytest)
- **Automated tests**: Tests-after
- **Framework**: pytest

### QA Policy
Cada task incluye escenarios QA ejecutables por agente. Evidencia en `.omo/evidence/task-{N}-{scenario}.{ext}`.

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — 2 tasks en paralelo):
├── Task 1: credit_checker.py — Módulo de verificación de créditos [quick]
└── Task 2: logging_config.py — Configuración de logging [quick]

Wave 2 (Core — 1 task, depende de Wave 1):
├── Task 3: daily_extract.py — Script principal [unspecified-high]

Wave 3 (Integración — 2 tasks en paralelo):
├── Task 4: Instalar crontab + verificar ejecución [quick]
└── Task 5: Ejecución de prueba — D-1 real [unspecified-high]
```

### Dependency Matrix
- **1**: - - 3, 4
- **2**: - - 3, 4
- **3**: 1, 2 - 4, 5
- **4**: 1, 2, 3 - -
- **5**: 3 - -

### Agent Dispatch Summary
- **Wave 1** (2 agents): T1 → `quick`, T2 → `quick`
- **Wave 2** (1 agent): T3 → `unspecified-high`
- **Wave 3** (2 agents): T4 → `quick`, T5 → `unspecified-high`

---

## TODOs

- [ ] 1. `credit_checker.py` — Módulo de verificación de créditos API

  **What to do**:
  - Crear `src/aeropredict/opensky/credit_checker.py`
  - Función `check_credits(endpoint_bucket: str = "flights") -> dict` que:
    - Hace un GET ligero a `/flights/arrival?airport=LEMD&begin=<epoch>&end=<epoch>` con rango de 1h
    - Lee cabeceras `X-Rate-Limit-Remaining` y `X-Rate-Limit-Retry-After-Seconds`
    - Devuelve `{"remaining": int, "retry_after": int | None, "reset_at": datetime | None}`
  - Función `can_extract(min_required: int = 2000) -> tuple[bool, dict]` que llama a check_credits y compara
  - Manejar errores: 429, 401, timeout, conexión
  - Usar el `OpenSkyClient` existente o `requests` directamente con token del `TokenManager`

  **Must NOT do**:
  - No modificar `client.py` ni `auth.py`
  - No hacer requests innecesarios (solo 1 request ligero)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Módulo pequeño y bien definido, ~40 líneas, lógica simple
  - **Skills**: None needed
  - **Skills Evaluated but Omitted**: N/A

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3)
  - **Blocks**: Task 3
  - **Blocked By**: None (can start immediately)

  **References**:
  - `src/aeropredict/opensky/client.py` — Patrón de requests, headers, manejo de errores HTTP
  - `src/aeropredict/opensky/auth.py` — TokenManager para obtener token
  - Official API: https://openskynetwork.github.io/opensky-api/rest.html#api-credits — Documentación de créditos y cabeceras

  **Acceptance Criteria**:
  - [ ] `from aeropredict.opensky.credit_checker import check_credits` funciona sin error
  - [ ] `check_credits()` devuelve dict con `remaining` (int)
  - [ ] Si hay 429, `retry_after` contiene segundos hasta reset
  - [ ] Todos los errores HTTP se manejan sin crash

  **QA Scenarios**:
  ```
  Scenario: Verificación de créditos exitosa
    Tool: Bash (python -c)
    Preconditions: OpenSky API disponible, token válido
    Steps:
      1. Ejecutar: python -c "from aeropredict.opensky.credit_checker import check_credits; print(check_credits())"
    Expected Result: Diccionario con 'remaining' > 0
    Evidence: .omo/evidence/task-1-credits-ok.txt

  Scenario: can_extract retorna False cuando créditos insuficientes
    Tool: Bash (python -c)
    Preconditions: Módulo instalado
    Steps:
      1. Ejecutar: python -c "from aeropredict.opensky.credit_checker import can_extract; ok, info = can_extract(min_required=999999); print(f'OK={ok}, remaining={info[\"remaining\"]}')"
    Expected Result: ok=False, remaining < min_required
    Evidence: .omo/evidence/task-1-credits-insufficient.txt
  ```

  **Commit**: YES
  - Message: `feat(opensky): add credit_checker module for API credit verification`
  - Files: `src/aeropredict/opensky/credit_checker.py`

---

- [ ] 2. `logging_config.py` — Configuración de logging para el script diario

  **What to do**:
  - Crear `src/aeropredict/opensky/logging_config.py`
  - Función `setup_daily_logger(name: str = "daily_extract") -> logging.Logger` que:
    - Crea directorio `data/logs/` si no existe
    - Configura logging a archivo `data/logs/daily_extract.log` con rotación (tamaño: 10MB, backup: 5)
    - Formato: `[timestamp] LEVEL [module] mensaje`
    - También output a stderr para depuración
  - Loggea a nivel INFO por defecto, DEBUG si variable `OPENSKY_LOG_LEVEL=DEBUG`

  **Must NOT do**:
  - No modificar logging existente en otros módulos
  - No afectar loggers de terceros

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: ~30 líneas, lógica de logging estándar
  - **Skills**: None needed

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3)
  - **Blocks**: Task 3
  - **Blocked By**: None (can start immediately)

  **References**:
  - Python logging docs: https://docs.python.org/3/library/logging.handlers.html#rotatingfilehandler

  **Acceptance Criteria**:
  - [ ] `setup_daily_logger()` es importable y configurable
  - [ ] Crea `data/logs/daily_extract.log` tras primer mensaje
  - [ ] Formato incluye timestamp, level, módulo, mensaje
  - [ ] Rotación funciona (forzar con tamaño pequeño en test)
  - [ ] Variable OPENSKY_LOG_LEVEL cambia nivel

  **QA Scenarios**:
  ```
  Scenario: Logger crea archivo de log
    Tool: Bash
    Preconditions: Módulo instalado
    Steps:
      1. python -c "from aeropredict.opensky.logging_config import setup_daily_logger; log = setup_daily_logger(); log.info('test message')"
      2. cat data/logs/daily_extract.log
    Expected Result: Archivo existe, contiene "test message" con formato [timestamp] INFO
    Evidence: .omo/evidence/task-2-log-file.txt
  ```

  **Commit**: YES (groups with 1)
  - Message: `feat(opensky): add daily extract logging configuration`
  - Files: `src/aeropredict/opensky/logging_config.py`

---

- [ ] 3. `daily_extract.py` — Script principal de extracción diaria

  **What to do**:
  - Crear `src/aeropredict/opensky/daily_extract.py`
  - Script ejecutable (`python -m aeropredict.opensky.daily_extract`) con función `main()`
  - Flujo:
    1. Setup logger desde `logging_config`
    2. Verificar créditos via `credit_checker.can_extract(min_required=2000)`
    3. Si créditos insuficientes: loggear `can_extract()` info + `retry_after` + exit
    4. Si créditos suficientes: determinar D-1 (ayer UTC) = `(datetime.now() - timedelta(days=1)).date()`
    5. Consultar Delta `silver/flights` para día D-1 → obtener set de aeropuertos ya cargados
    6. Obtener todos los ICAO de `config.get_airport_icao_codes()`
    7. Filtrar: solo aeropuertos españoles (los que tienen país "España" en AEROPUERTOS)
    8. Calcular missing = total - ya_existentes
    9. Para cada missing:
       - Consultar arrivals + departures con 5s de delay entre requests
       - Si 404: loggear "sin datos" y continuar
       - Si otros errores: loggear warning y continuar
       - Llamar `write_flights_silver` con resultados parseados
       - `time.sleep(5)` entre cada par arrival+departure
    10. Si sobran créditos después de D-1, intentar D-2, D-3... hasta agotar créditos
  - Hacer el script invocable como `python -m aeropredict.opensky.daily_extract`

  **Must NOT do**:
  - No modificar `extract_flights.py`, `storage.py`, `client.py`, `config.py`
  - No importar modelos o funciones no existentes
  - No hardcodear rutas (usar `get_delta_root()`)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Script de ~120 líneas con lógica de coordinación multi-paso, manejo de errores, integración con Delta
  - **Skills**: None needed

  **Parallelization**:
  - **Can Run In Parallel**: YES (with 1, 2)
  - **Parallel Group**: Wave 1 (with Tasks 1, 2)
  - **Blocks**: Task 4, Task 5
  - **Blocked By**: Tasks 1, 2

  **References**:
  - `src/aeropredict/opensky/extract_flights.py` — fetch_arrivals_raw, fetch_departures_raw, parse_flight_list
  - `src/aeropredict/opensky/storage.py` — write_flights_silver
  - `src/aeropredict/opensky/config.py` — get_airport_icao_codes, AEROPUERTOS, get_delta_root
  - `src/aeropredict/opensky/credit_checker.py` — can_extract (Task 1)
  - `src/aeropredict/opensky/logging_config.py` — setup_daily_logger (Task 2)
  - `data/raw/silver/flights/` — Tabla Delta existente

  **Acceptance Criteria**:
  - [ ] `python -m aeropredict.opensky.daily_extract --help` muestra ayuda
  - [ ] `python -m aeropredict.opensky.daily_extract --dry-run` muestra plan sin ejecutar
  - [ ] `python -m aeropredict.opensky.daily_extract` ejecuta extracción (si hay créditos)
  - [ ] Si no hay créditos: loggea mensaje claro con `retry_after`
  - [ ] No consulta aeropuertos ya presentes en Delta para D-1
  - [ ] Log por aeropuerto: `LEMD: 120 arrivals + 115 departures = 235 vuelos`
  - [ ] Resumen final: `Total: 2340 vuelos de 23 aeropuertos en 320s`
  - [ ] Si no quedan créditos a mitad de extracción, loggea y termina gracefulmente

  **QA Scenarios**:
  ```
  Scenario: Dry-run muestra plan
    Tool: Bash
    Preconditions: Módulos instalados
    Steps:
      1. python -m aeropredict.opensky.daily_extract --dry-run
    Expected Result: Muestra "DRY RUN: extraería X aeropuertos para YYYY-MM-DD" sin hacer requests reales
    Evidence: .omo/evidence/task-3-dry-run.txt

  Scenario: Sin créditos, loggea retry_after
    Tool: Bash
    Preconditions: Variable OPENSKY_SIMULATE_NO_CREDITS=1
    Steps:
      1. OPENSKY_SIMULATE_NO_CREDITS=1 python -m aeropredict.opensky.daily_extract
    Expected Result: Log "Créditos insuficientes: 0 remaining. Retry after: Xs. Siguiente ventana estimada: ..."
    Evidence: .omo/evidence/task-3-no-credits.txt
  ```

  **Commit**: YES (groups with 1, 2)
  - Message: `feat(opensky): add daily extract script with credit management`
  - Files: `src/aeropredict/opensky/daily_extract.py`

---

- [ ] 4. Instalar crontab + verificar ejecución

  **What to do**:
  - Añadir entrada crontab para ejecutar el script 2x/día:
    ```
    # Extracción diaria OpenSky - D-1 historical flights
    0 6 * * * cd /home/devcontainers/aeropredict && .conda/envs/aeropredict/bin/python -m aeropredict.opensky.daily_extract >> /home/devcontainers/aeropredict/data/logs/cron_output.log 2>&1
    0 19 * * * cd /home/devcontainers/aeropredict && .conda/envs/aeropredict/bin/python -m aeropredict.opensky.daily_extract >> /home/devcontainers/aeropredict/data/logs/cron_output.log 2>&1
    ```
  - Verificar crontab entry con `crontab -l`
  - Verificar que el script es ejecutable: `python -m aeropredict.opensky.daily_extract --help`
  - Probar ejecución manual del script con `--dry-run` para verificar sin消耗 créditos

  **Must NOT do**:
  - No modificar crontab de otros usuarios
  - No ejecutar el script real si no hay créditos (solo dry-run)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Tarea de configuración, 5 minutos
  - **Skills**: None needed

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on 3)
  - **Parallel Group**: Sequential
  - **Blocks**: Task 5
  - **Blocked By**: Tasks 1, 2, 3

  **References**:
  - `man 5 crontab` — Formato crontab
  - `src/aeropredict/opensky/daily_extract.py` — Script a ejecutar

  **Acceptance Criteria**:
  - [ ] `crontab -l` muestra las 2 entradas
  - [ ] `python -m aeropredict.opensky.daily_extract --help` funciona
  - [ ] `python -m aeropredict.opensky.daily_extract --dry-run` se ejecuta sin error

  **QA Scenarios**:
  ```
  Scenario: Crontab instalado correctamente
    Tool: Bash
    Preconditions: Script daily_extract.py existe
    Steps:
      1. crontab -l
    Expected Result: Muestra las 2 entradas con las rutas correctas
    Evidence: .omo/evidence/task-4-crontab.txt

  Scenario: Script ayuda funciona
    Tool: Bash
    Steps:
      1. python -m aeropredict.opensky.daily_extract --help
    Expected Result: Muestra uso del script con opciones --dry-run, --days, etc.
    Evidence: .omo/evidence/task-4-help.txt
  ```

  **Commit**: NO (no hay código nuevo, solo configuración)

---

## Final Verification Wave

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, run command). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files in .omo/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `ruff check src/aeropredict/opensky/` + `python -c "from aeropredict.opensky.daily_extract import main"`. Review for: bare excepts, hardcoded paths, missing error handling, unused imports.
  Output: `Lint [PASS/FAIL] | Imports [PASS/FAIL] | VERDICT`

- [ ] F3. **Real Manual QA** — `unspecified-high`
  Execute QA scenarios from ALL tasks. Verify dry-run, credit checking, log rotation, crontab entries.
  Output: `Scenarios [N/N pass] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — `deep`
  Verify all tasks match the plan. Check for scope creep (e.g., no UI, no notifications, no CLI entry point changes).
  Output: `Tasks [N/N compliant] | Contamination [CLEAN] | VERDICT`

---

## Commit Strategy

- **1-3**: `feat(opensky): add daily extract pipeline` — credit_checker.py, logging_config.py, daily_extract.py

---

## Success Criteria

### Verification Commands
```bash
# Verificar instalación
python -m aeropredict.opensky.daily_extract --help

# Dry-run (sin consumir créditos)
python -m aeropredict.opensky.daily_extract --dry-run

# Verificar crontab
crontab -l

# Verificar log
cat data/logs/daily_extract.log
```

### Final Checklist
- [ ] Script `daily_extract.py` funcional con --help y --dry-run
- [ ] Crontab instalado: 6:00 y 19:00 UTC
- [ ] Logging funciona y escribe a archivo
- [ ] Credit checker funcional
- [ ] Skip aeropuertos ya cargados
- [ ] Delay 5s entre peticiones
