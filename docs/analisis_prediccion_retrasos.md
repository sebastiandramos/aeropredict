# Predicción de retrasos — Análisis de datos disponibles

> **Objetivo**: Predecir la hora de llegada (y por tanto el retraso) de vuelos
> con origen o destino en aeropuertos españoles.

---

## 1. Datos que ya tenemos

### Silver (MongoDB) — Flight

| Campo | Tipo | Utilidad |
|-------|------|----------|
| `icao24` | str | Identificador único de aeronave. Permite tracking por cola de rotación |
| `callsign` | str \| None | Prefijo IATA/ICAO → aerolínea |
| `first_seen` | datetime | Proxy de despegue (primera detección en el rango) |
| `last_seen` | datetime | Proxy de aterrizaje (última detección en el rango) |
| `est_departure_airport` | str \| None | Aeropuerto de origen estimado |
| `est_arrival_airport` | str \| None | Aeropuerto de destino estimado |
| `est_departure_airport_horiz_distance` | float \| None | Distancia horizontal a origen ← fiabilidad |
| `est_departure_airport_vert_distance` | float \| None | Distancia vertical a origen |
| `est_arrival_airport_horiz_distance` | float \| None | Distancia horizontal a destino |
| `est_arrival_airport_vert_distance` | float \| None | Distancia vertical a destino |
| `departure_airport_candidates_count` | int \| None | Candidatos evaluados para origen |
| `arrival_airport_candidates_count` | int \| None | Candidatos evaluados para destino |
| `flight_date` | date | Fecha del vuelo (feature temporal) |

### Gold (PostgreSQL) — Agregaciones

| Tabla | Contenido | Utilidad |
|-------|-----------|----------|
| `daily_airport_traffic` | arrivals/departures por aeropuerto+fecha | Congestión del aeropuerto |
| `route_density` | pares origen-destino con frecuencia | Densidad de esa ruta |
| `hourly_distribution` | distribución horaria por aeropuerto+fecha | Franjas de alta demanda |

---

## 2. Features derivables YA (sin datos externos)

| Feature | Fuente | Cálculo |
|---------|--------|---------|
| **hora_salida** | `first_seen` | Hora del día + minuto de salida |
| **hora_llegada** | `last_seen` | Hora del día + minuto de llegada |
| **dia_semana** | `first_seen` | Lunes=0, Domingo=6 |
| **mes** | `first_seen` | Estacionalidad |
| **aerolinea** | `callsign[:3]` | Mapear callsign → aerolínea (tabla interna, ej: IBE=Iberia, RYR=Ryanair, VLG=Vueling) |
| **distancia_ruta** | Lat/Lon fijo por ICAO airport code | Distancia ortodrómica entre aeropuertos |
| **duracion_vuelo** | `last_seen - first_seen` | Duración real observada (proxy) |
| **retraso_vuelo_anterior** | mismo `icao24`, mismo día | Si el avión llegó tarde al origen, el siguiente también |
| **trafico_diario_origen** | Gold `daily_airport_traffic` | Cuántos vuelos operó el aeropuerto origen ese día |
| **trafico_diario_destino** | Gold `daily_airport_traffic` | Cuántos vuelos operó el aeropuerto destino ese día |
| **frecuencia_ruta** | Gold `route_density` | Cuán habitual es esa ruta |
| **es_festivo** | Calendario público | Días festivos nacionales + autonómicos |
| **es_puente** | Calendario | Viernes+puente, lunes+puente |

---

## 3. Lo que falta (datos externos necesarios)

### 3.1 Schedule programado — ⭐ ESENCIAL

OpenSky **no** proporciona horarios programados. **Sin schedule no podemos calcular retraso** (necesitamos `scheduled_departure` / `scheduled_arrival`).

**Opciones:**

| Fuente | Precio | Coverage | Enlace | Notas |
|--------|--------|----------|--------|-------|
| **AviationStack** | Gratis 100 req/mes | Global | [aviationstack.com/signup/free](https://aviationstack.com/signup/free) — registro inmediato, API key al instante. Docs: [docs.apilayer.com/aviationstack](https://docs.apilayer.com/aviationstack/docs/api-documentation) | Bien documentada, schedules + flights |
| **AeroAPI (FlightAware)** | Free tier limitado | Global | [flightaware.com/commercial/aeroapi](https://flightaware.com/commercial/aeroapi/) | Muy completa, cara |
| **OpenSkills API** | Gratis | Rutas aéreas | [open-skills.net](https://open-skills.net/) | Datos abiertos, no tiempo real |

**Recomendación**: AviationStack (free tier 100 req/mes suficiente para empezar).

### 3.2 Tipo de aeronave (A320, B738, B788...)

Afecta velocidad de crucero, autonomía, consumo. Influye en duración esperada.

**Fuentes:**
- [OpenSky aircraft database](https://opensky-network.org/data/aircraft) — gratuita, mapea `icao24 → aircraft type`. Descarga CSV desde [Scientific Datasets](https://opensky-network.org/data/datasets). No requiere API key.
- [OpenSky metadata CSV directo](https://opensky-network.org/datasets/metadata/) — snapshots mensuales del aircraft database.

### 3.3 Meteorología

Viento, lluvia, visibilidad, temperatura en origen/destino a la hora del vuelo.

**Fuentes:**
| Fuente | Precio | Coverage | Enlace | Notas |
|--------|--------|----------|--------|-------|
| **Open-Meteo** | Gratis | Global | [open-meteo.com/en/docs](https://open-meteo.com/en/docs) — No requiere API key. Límite: 10.000 calls/día gratis. Docs integradas en la web | Pronóstico + histórico. API sencilla por coordenadas |
| **AEMET** | Gratis (API key) | España | [opendata.aemet.es](https://opendata.aemet.es/centrodedescargas/inicio) — API key: [altaUsuario](https://opendata.aemet.es/centrodedescargas/altaUsuario) | Datos oficiales España. Dos pasos por petición |

**Recomendación**: Open-Meteo (sin API key, límite generoso, calls por coordenadas del aeropuerto).

### 3.4 Estado de aeropuertos (retrasos en tierra)

Congestión actual del aeropuerto (retrasos ATC, condiciones de pista).

| Fuente | Precio | Coverage | Enlace |
|--------|--------|----------|--------|
| **AviationStack** | Misma API | Global | [aviationstack.com](https://aviationstack.com/) — endpoint `/flights` incluye `delay` en la respuesta |
| **Eurocontrol DDR** | Gratis (registro) | Europa | [eurocontrol.int/ddr](https://www.eurocontrol.int/ddr) — muy detallado pero complejo |

---

## 4. Feature pipeline completo (visión)

```
Raw Data                    Features                          Target
─────────                  ────────                          ──────
Schedule (AviationStack) → scheduled_departure_time     scheduled_arrival_time
                            scheduled_arrival_time             │
OpenSky Flight          → hora_del_dia                        │
                            dia_semana                         │
                            aerolinea                       [retraso] = 
                            distancia_ruta            actual_arrival -
                            duracion_vuelo              scheduled_arrival
                            retraso_anterior                    │
icao24 → metadata DB    → tipo_aeronave                        │
                            velocidad_crucero                   │
Open-Meteo              → viento_origen                        │
                            viento_destino                      │
                            lluvia                              │
Gold (PG)               → trafico_diario                       │
                            densidad_ruta                       │
                            hora_punta                          │
Calendario              → es_festivo                           │
```

---

## 5. Próximos pasos (priorizados)

1. **AviationStack**: Conseguir API key, integrar schedules → target de retraso
2. **Aircraft metadata**: Descargar DB de OpenSky → tipo de aeronave
3. **Feature engineering**: Computar rotación de aeronave (retraso en cadena)
4. **Open-Meteo**: Añadir datos meteorológicos
5. **Entrenar modelo baseline**: Con features actuales + schedule + rotación

---

## 6. Stack ML propuesto

| Componente | Herramienta |
|------------|-------------|
| Feature store | MongoDB (silver) + PostgreSQL (gold) |
| Feature engineering | Python (+ pandas, numpy) |
| Modelo | LightGBM / XGBoost (gradient boosting) |
| Experiment tracking | MLflow |
| Serving | FastAPI endpoint |
| Orquestación | Scripts Python + cron / Airflow ligero |

---

## 7. Estrategia de calidad de datos

### 7.1 Desduplicación

La pipeline desduplica vuelos en dos puntos:

| Capa | Método | Clave de desduplicación |
|------|--------|-------------------------|
| Bronze → Silver | Código Python (`bronze_to_silver._read_bronze_flights`) | `(icao24, first_seen.timestamp(), callsign)` |
| Silver → Gold | PostgreSQL `ON CONFLICT` / upsert | Clave única en tabla destino |

**Clave `(icao24, first_seen_timestamp, callsign)`:**

| Componente | Por qué |
|------------|---------|
| `icao24` | Identificador único de la aeronave. Misma aeronave opera múltiples vuelos al día. |
| `first_seen_timestamp` | Proxy de hora de despegue. Diferencia vuelos distintos del mismo avión. |
| `callsign` | Número de vuelo. Desambigua casos donde el mismo avión aparece dos veces con el mismo `first_seen` (ej. datos duplicados por solapamiento de ventanas de extracción). |

Sin `callsign` en la clave, dos extracciones de la misma ventana de OpenSky producirían filas duplicadas. Sin `first_seen`, un avión que opera dos vuelos el mismo día aparecería como uno solo.

### 7.2 Manejo de valores nulos

Tres estrategias según el campo:

**1. Drop (no se admite nulo):**

| Campo | Motivo |
|-------|--------|
| `icao24` | Identificador obligatorio. Sin él no hay registro de vuelo. |

**2. Pass-through (nulo se almacena como NULL):**

| Grupo | Campos | Justificación |
|-------|--------|---------------|
| Callsign | `callsign` | OpenSky no siempre lo proporciona; el vuelo sigue siendo útil. |
| Aeropuertos | `est_departure_airport`, `est_arrival_airport` | La estimación puede fallar; se almacena NULL y se filtra en feature engineering. |
| Timestamps | `first_seen`, `last_seen` | Raro pero posible; el vuelo se ingiere aunque falten. |
| Distancias | `departure_airport_horiz_distance`, `departure_airport_vert_distance`, `arrival_airport_horiz_distance`, `arrival_airport_vert_distance` | Dependen de la calidad de la estimación de aeropuerto. |
| Candidatos | `departure_airport_candidates_count`, `arrival_airport_candidates_count` | Informativos; NULL cuando no se evaluaron candidatos. |
| Metadatos aeronave | `aircraft_type`, `aircraft_manufacturer`, `aircraft_operator`, `aircraft_age_years` | Dependen de la base de datos de aeronaves de OpenSky (no siempre disponible). |
| Meteorología | `dep_temperature`, `dep_precipitation`, `dep_wind_speed`, `dep_visibility`, `arr_temperature`, `arr_precipitation`, `arr_wind_speed`, `arr_visibility` | Campo opcional; NULL cuando Open-Meteo no tiene datos para esa hora. |
| Schedules | `schedule_source` | NULL cuando no hay datos de horario programado. |

**3. Imputado en feature engineering (no en ingesta):**

| Feature | Valor por defecto | Dónde se imputa |
|---------|-------------------|-----------------|
| `route_daily_traffic` | 0 | `build_feature_store.py` (LEFT JOIN con gold) |
| `route_total_density` | 0 | `build_feature_store.py` |
| `departure_airport_hourly_traffic` | 0 | `build_feature_store.py` |
| `arrival_airport_hourly_traffic` | 0 | `build_feature_store.py` |
| `departure_hour` | NULL (se omite la fila en entrenamiento si es crítica) | Feature engineering |

### 7.3 Validación con esquemas Pydantic v2

Todos los modelos en `schemas.py` usan `frozen=True` y `extra="forbid"` para detectar derivas de datos temprano. La validación es **no bloqueante**: devuelve `(valid, invalid)` y registra el número de filas rechazadas, pero no detiene la pipeline.

```
Bronze (Delta) ──→ validate_flights() ──→ valid → MongoDB (Silver)
                             │
                             └── invalid → log + continue
```

Esto permite que errores de formato en una minoría de filas no bloqueen la ingesta del resto.

**Validadores por capa:**

| Capa | Validador | Modelo Pydantic | Puntos de escritura |
|------|-----------|-----------------|---------------------|
| Bronze | `validate_state_vectors` | `StateVector` | Delta Lake (archivos Parquet) |
| Bronze | `validate_flights` | `OpenSkyFlight` | Delta Lake |
| Silver | `validate_flights` | `FlightDocument` | MongoDB `flights` |
| Silver | `validate_weather` | `WeatherDocument` | MongoDB `weather` |
| Silver | `validate_aircraft` | `AircraftDocument` | MongoDB `aircraft` |
| Silver | `validate_schedules` | `ScheduleDocument` | MongoDB `schedules` |
| Gold | `validate_feature_store` | `FeatureStoreRow` | PostgreSQL `gold.feature_store` |

### 7.4 Normalización

Reglas aplicadas por `field_validator` en cada capa:

| Regla | Validación | Ejemplo |
|-------|-----------|---------|
| Códigos aeropuerto | Mayúsculas + 4 letras | `"lemd"` → `"LEMD"` |
| Timestamps | Todos a UTC | `2025-06-15T10:00:00-05:00` → `2025-06-15T15:00:00Z` |
| Callsign | Mayúsculas + 1-10 alfanuméricos | `"ibe1234"` → `"IBE1234"` |
| ICAO24 | Mayúsculas + 6 hex | `"abcdef"` → `"ABCDEF"` |
| Distancias | No negativas | `-50.0` → `ValueError` |
| Candidatos | No negativos | `-1` → `ValueError` |
| Coordenadas | Lat `[-90,90]`, Lon `[-180,180]` | `100.0` → `ValueError` |
| Porcentajes | `[0, 100]` | `150.0` → `ValueError` |

---

## 8. Roadmap de feature engineering

### 8.1 Features actuales (15+) — lógica de derivación

| Feature | Tipo | Fuente | Lógica de derivación |
|---------|------|--------|----------------------|
| `icao24` | str (PK) | OpenSky | Identificador ICAO 24-bit, normalizado a 6 hex mayúsculas |
| `flight_date` | date (PK) | OpenSky | Fecha del vuelo (`first_seen` sin hora) |
| `callsign` | str | OpenSky | Prefijo IATA/ICAO. Trim + uppercase vía validador |
| `departure_airport` | str | OpenSky | `est_departure_airport`. Validado 4 letras mayúsculas |
| `arrival_airport` | str | OpenSky | `est_arrival_airport`. Validado 4 letras mayúsculas |
| `delay_minutes` | float | Schedule real | `actual_arrival - scheduled_arrival` (requiere schedule). Target del modelo |
| `airborne_minutes` | float | OpenSky | `(last_seen - first_seen) / 60` en minutos |
| `departure_hour` | int | OpenSky | `first_seen.hour` — hora del día (0-23) |
| `day_of_week` | int | OpenSky | `first_seen.isoweekday()` — lunes=1, domingo=7 |
| `month` | int | OpenSky | `first_seen.month` — estacionalidad (1-12) |
| `aircraft_type` | str | OpenSky aircraft DB | `icao24 → typecode` (ej. A320, B738). JOIN con `gold.aircraft` |
| `aircraft_manufacturer` | str | OpenSky aircraft DB | `icao24 → manufacturer` (ej. Airbus, Boeing) |
| `aircraft_operator` | str | OpenSky aircraft DB | `icao24 → operator` (ej. Iberia, Ryanair) |
| `aircraft_age_years` | float | OpenSky aircraft DB | `year(today) - year(first_flight_date)`. Desde `gold.aircraft.first_flight_date` |
| `route_daily_traffic` | int | Gold agg | COUNT(*) de vuelos en misma ruta `(origen, destino)` mismo día |
| `route_total_density` | int | Gold agg | COUNT(*) histórico de vuelos en esa ruta (tabla `route_density`) |
| `departure_airport_hourly_traffic` | int | Gold agg | COUNT(*) de salidas en mismo aeropuerto + hora (tabla `hourly_distribution`) |
| `arrival_airport_hourly_traffic` | int | Gold agg | COUNT(*) de llegadas en mismo aeropuerto + hora |
| `dep_temperature` | float | Open-Meteo | Temperatura a 2m en aeropuerto origen a la hora del vuelo |
| `dep_precipitation` | float | Open-Meteo | Precipitación en origen (mm) |
| `dep_wind_speed` | float | Open-Meteo | Velocidad viento a 10m en origen (km/h) |
| `dep_visibility` | float | Open-Meteo | Visibilidad en origen (m) |
| `arr_temperature` | float | Open-Meteo | Temperatura en destino |
| `arr_precipitation` | float | Open-Meteo | Precipitación en destino |
| `arr_wind_speed` | float | Open-Meteo | Velocidad viento en destino |
| `arr_visibility` | float | Open-Meteo | Visibilidad en destino |
| `schedule_source` | str | Schedule API | `aerodatabox` o `aviationstack`. Origen del schedule. |

**Nota**: `delay_minutes` requiere datos de schedule programado. Estos provienen del web scraping realizado por un compañero (AviationStack / AeroDataBox). Sin schedule no es posible calcular el retraso real. Es una dependencia externa.

### 8.2 Features futuras (5+)

| Feature | Lógica propuesta | Fuente necesaria | Prioridad |
|---------|-----------------|------------------|-----------|
| **Índice de congestión del aeropuerto** | Ratio `vuelos_actuales / capacidad_histórica` por aeropuerto+hora. Combina `hourly_distribution` con la capacidad del aeropuerto (número de slots ATC). | Gold `hourly_distribution` + Eurocontrol DDR (slots) | Alta |
| **Propagación de retraso en cadena** | Si el vuelo anterior del mismo `icao24` llegó con retraso, el siguiente tiene alta probabilidad de retraso. Feature: `delay_previous_leg_minutes`. | Misma aeronave, vuelo anterior del día. JOIN de FeatureStore consigo mismo por `(icao24, fecha)` ordenado por `departure_hour`. | Alta |
| **Clusters de edad de aeronave** | Agrupar `aircraft_age_years` en categorías: joven (<5), medio (5-15), viejo (15+). Feature categórica. | `aircraft_age_years` ya disponible | Media |
| **Índice de severidad meteorológica** | Combinación ponderada de viento + precipitación + visibilidad en un solo score (0-1). Puede usar reglas simples o un mini-modelo. | Datos meteorológicos ya disponibles | Media |
| **Patrones estacionales** | Features de calendario: semana del año, día estacional (1-365), cuartil del año, períodos vacacionales (Semana Santa, verano, Navidad). | `flight_date` ya disponible | Baja |
| **Retraso medio histórico de la ruta** | Media móvil de retraso en la misma ruta `(origen, destino)` para los últimos 7/14/30 días. Requiere training continuo o tabla de agregación. | Gold `gold.flights` + schedules históricos | Baja |

### 8.3 Dependencias externas

| Feature | Dependencia | Estado |
|---------|-------------|--------|
| `delay_minutes` | Schedule programado (AviationStack / AeroDataBox) | Web scraping del compañero. Pendiente de integración. |
| `aircraft_type`, `manufacturer`, `operator`, `age` | OpenSky aircraft database (CSV mensual) | Implementado en `collect_aircraft_db.py` |
| `dep_*`, `arr_*` (meteo) | Open-Meteo API | Implementado en `collect_weather.py` |

---

## 9. Estrategia de pruebas

### 9.1 Cobertura actual y objetivo

| Métrica | Valor actual | Objetivo |
|---------|-------------|----------|
| Cobertura de código | 46% | 70% |
| Tests totales | 232 | — |
| Archivos de test | 14 | — |
| Tiempo de ejecución | ~7s | <30s |

### 9.2 Distribución de tests por archivo

| Archivo | Tests | Lo que valida |
|---------|-------|---------------|
| `test_validators.py` | 17 | Validación Pydantic no bloqueante: `validate_flights`, `validate_weather`, `validate_aircraft`, `validate_feature_store`, `validate_schedules`, `validate_state_vectors` |
| `test_data_quality.py` | 37 | Desduplicación (clave `(icao24, first_seen, callsign)`), normalización (uppercase, UTC, no negativos), manejo de nulos (pass-through vs drop), completitud (≥80% filas no nulas en columnas críticas) |
| `test_feature_completeness.py` | 27 | Schema `FeatureStoreRow`: 28 campos presentes, frozen, extra-forbid, rangos (`departure_hour` 0-23, `day_of_week` 1-7, `month` 1-12), nulos opcionales, serialización JSON |
| `test_schemas.py` | — | Validación de modelos Pydantic (todos los `*Document`, `BronzeFlight`, `GoldFlight`, etc.) |
| `test_extract_to_bronze.py` | — | Extracción mock → Delta Lake (Bronze) |
| `test_extract_flights.py` | — | Parseo de respuestas OpenSky |
| `test_bronze_to_silver.py` | — | Transformación Bronze Delta → MongoDB Silver |
| `test_silver_to_gold.py` | — | Agregaciones Silver → Gold (MongoDB → PostgreSQL) |
| `test_entities.py` | — | Entidades Gold (flights, aircraft, weather) |
| `test_feature_store.py` | — | Feature store: JOIN de múltiples fuentes, imputación, escritura PostgreSQL |
| `test_storage_silver.py` | — | Operaciones MongoDB (CRUD, checkpoints) |
| `test_storage_gold.py` | — | Operaciones PostgreSQL (CRUD, upserts) |
| `test_models.py` | — | Modelos de negocio (validadores de negocio, no solo esquema) |
| `test_config.py` | — | Configuración y variables de entorno |

### 9.3 Cómo validan los tests cada paso de la pipeline

| Paso de pipeline | Tests que lo cubren | Mecanismo de validación |
|-----------------|---------------------|------------------------|
| **Extract → Bronze** | `test_extract_to_bronze`, `test_extract_flights` | Datos mock → verificar escritura Delta Lake + parseo correcto |
| **Bronze → Silver** | `test_bronze_to_silver`, `test_schemas` | Flight dicts → `BronzeFlight.model_validate` → `FlightDocument` |
| **Silver → Gold** | `test_silver_to_gold`, `test_entities`, `test_storage_gold` | Datos MongoDB mock → verificar agregaciones y upserts PostgreSQL |
| **Feature store** | `test_feature_store`, `test_feature_completeness` | Feature dicts → `FeatureStoreRow.model_validate` → verificar campos, rangos, nulos |
| **Validación** | `test_validators` | Filas válidas + inválidas → verificar split correcto y logging |
| **Calidad de datos** | `test_data_quality` | Datos con nulos/repetidos → verificar dedup, normalización, completitud ≥80% |

### 9.4 Umbrales de aceptación

| Condición | Umbral | Medido por |
|-----------|--------|------------|
| Cobertura de código | ≥70% | `pytest --cov=src/aeropredict --cov-fail-under=70` |
| Tests pasados | 100% | `pytest --tb=short -q` (0 failures) |
| Completitud columnas críticas | ≥80% | `test_data_quality::TestCompleteness::test_critical_columns_above_80_pct` |
| Columnas timestamps | ≥80% | `test_data_quality::TestCompleteness::test_timestamp_columns_above_80_pct` |
| `icao24` nulo | 0% | `test_data_quality::TestCompleteness::test_all_columns_completeness_profile` |
| Campos extra en schema | 0 (forbidden) | `test_feature_completeness::TestSchemaCompleteness::test_model_forbids_extra_fields` |
| Congelación de instancias | Sí (frozen) | `test_feature_completeness::TestSchemaCompleteness::test_model_is_frozen` |
| Esquemas rechazan valores inválidos | 100% | Tests de rangos en `test_feature_completeness::TestFeatureRanges` |
