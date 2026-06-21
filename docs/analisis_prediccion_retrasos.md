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
