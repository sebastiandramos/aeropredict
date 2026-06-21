# MongoDB Schema — `aeropredict`

---

## ✈️ `aircraft` (579,548 docs)

Catálogo de aeronaves con código OACI.

| Campo | Tipo | Descripción |
|---|---|---|
| `_id` | ObjectId | Identificador único |
| `icao24` | string | Código OACI de 24 bits de la aeronave |
| `registration` | string | Matrícula |
| `manufacturer` | string | Fabricante |
| `model` | string | Modelo |
| `typecode` | string | Código de tipo |
| `serial_number` | string | Número de serie |
| `line_number` | string | Número de línea |
| `icao_aircraft_type` | string | Tipo de aeronave OACI |
| `operator` | string | Operador |
| `operator_callsign` | string | Callsign del operador |
| `operator_icao` | string | Código OACI del operador |
| `operator_iata` | string | Código IATA del operador |
| `first_flight_date` | string | Fecha del primer vuelo |
| `ingested_at` | Date | Fecha de ingesta en BD |

---

## 🛩️ `flights` (80,256 docs)

Vuelos detectados por OpenSky Network.

| Campo | Tipo | Descripción |
|---|---|---|
| `_id` | ObjectId | Identificador único |
| `icao24` | string | Código OACI de la aeronave |
| `callsign` | string | Indicativo del vuelo |
| `first_seen` | Date | Primera detección |
| `last_seen` | Date | Última detección |
| `est_departure_airport` | string | Aeropuerto de origen estimado (OACI) |
| `est_arrival_airport` | string | Aeropuerto de destino estimado (OACI) |
| `departure_airport_horiz_distance` | number | Distancia horizontal al origen (m) |
| `departure_airport_vert_distance` | number | Distancia vertical al origen (m) |
| `arrival_airport_horiz_distance` | number | Distancia horizontal al destino (m) |
| `arrival_airport_vert_distance` | number | Distancia vertical al destino (m) |
| `departure_airport_candidates_count` | number | Nº de candidatos a origen |
| `arrival_airport_candidates_count` | number | Nº de candidatos a destino |
| `flight_date` | Date | Fecha del vuelo |
| `ingested_at` | Date | Fecha de ingesta en BD |

---

## 📋 `schedules` (144 docs)

Horarios de vuelos programados (fuente: AerodataBox).

| Campo | Tipo | Descripción |
|---|---|---|
| `_id` | ObjectId | Identificador único |
| `source` | string | Fuente de datos |
| `callsign` | string | Indicativo del vuelo |
| `flight_date` | string | Fecha del vuelo |
| `flight_status` | string | Estado (Ej: EnRoute) |
| `departure_airport` | string | Aeropuerto de origen (OACI) |
| `departure_scheduled` | string | Salida programada |
| `departure_actual` | string | Salida real |
| `departure_terminal` | string | Terminal de salida |
| `departure_gate` | string | Puerta de embarque |
| `arrival_airport` | string | Aeropuerto de destino (OACI) |
| `arrival_scheduled` | string | Llegada programada |
| `arrival_actual` | string | Llegada real |
| `arrival_terminal` | string (nullable) | Terminal de llegada |
| `arrival_gate` | string | Puerta de llegada |
| `airline_name` | string | Nombre de la aerolínea |
| `airline_icao` | string | Código OACI de la aerolínea |
| `aircraft_type` | string | Tipo de aeronave |
| `aircraft_reg` | string | Matrícula de la aeronave |
| `ingested_at` | Date | Fecha de ingesta en BD |

---

## 🌤️ `weather` (19,944 docs)

Datos meteorológicos por aeropuerto.

| Campo | Tipo | Descripción |
|---|---|---|
| `_id` | ObjectId | Identificador único |
| `airport_code` | string | Código OACI del aeropuerto |
| `timestamp` | string | Marca de tiempo |
| `flight_date` | string | Fecha del vuelo |
| `temperature_2m` | number | Temperatura a 2m (°C) |
| `precipitation` | number | Precipitación (mm) |
| `wind_speed_10m` | number | Velocidad del viento a 10m (km/h) |
| `wind_gusts_10m` | number | Ráfagas de viento a 10m (km/h) |
| `visibility` | number (nullable) | Visibilidad |
| `cloud_cover` | number | Cobertura nubosa (%) |
| `relative_humidity_2m` | number | Humedad relativa a 2m (%) |
| `ingested_at` | Date | Fecha de ingesta en BD |

---

## Colecciones vacías

| Colección | Docs | Descripción |
|---|---|---|
| `track_waypoints` | 0 | Waypoints de trayectoria |
| `state_vectors` | 0 | Vectores de estado en tiempo real |
