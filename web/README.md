# AeroPredict — web app (frontend)

React + Vite + TypeScript. Interfaz de predicción de retrasos de vuelos.

## Qué hace

Formulario de vuelo/ruta (origen, destino, aerolínea, fecha y hora; campos
avanzados opcionales) que calcula la distancia estimada y consulta la API de
predicción para mostrar:

- **Probabilidad / retraso previsto** (severidad: puntual / moderado / severo).
- **Hora estimada de llegada** (ETA) y componente de retraso.
- **Factores explicativos** desglosados (indicativos — derivados del formulario,
  la API no devuelve importancia de features).

Cubre las tarjetas de Trello de *App / Producto*: definición de pantalla, input
de vuelo/ruta, mostrar probabilidad de retraso y mostrar factores explicativos.

## Datos

- `src/data/airports.ts` — aeropuertos (ICAO, nombre, ciudad, país, lat/lon) y
  aerolíneas comunes, usados para los selectores del formulario y el cálculo de
  distancia (haversine).
- `src/lib/` — capa de servicio: cliente HTTP único (`api.ts`), modo mock
  determinista (`mock.ts`), router mock/real (`service.ts`) y tipos del
  contrato de la API (`types.ts`).

## Arrancar

Requiere Node 22+. Instalar con `npm install`.

- **Modo demo (por defecto, sin backend)**: `npm run dev`. Usa datos simulados.
- **API real**: crea `.env` desde `.env.example` con `VITE_USE_MOCK=false`.
  En dev usa `VITE_API_BASE_URL=/api` (proxy de Vite → `http://localhost:8000`,
  evita CORS). Si la API exige clave, define `VITE_API_KEY`.

Build: `npm run build` → `web/dist/`. Lint: `npm run lint`.

## Contrato de la API consumida

| Método | Ruta | Request | Response |
|---|---|---|---|
| GET | `/health` | — | `{status, model_version}` |
| POST | `/predict/delay` | `DelayFeatures` | `{predicted_delay_minutes, confidence, model_version}` |
| POST | `/predict/eta` | `{scheduled_arrival, features}` | `{estimated_arrival_time, confidence, delay_component, disruption_likely}` |

`DelayFeatures`: `hour_of_day`, `day_of_week`, `airline`, `route_distance`
(obligatorios); `aircraft_type`, `aircraft_manufacturer`, `aircraft_operator`,
`weather_temperature_2m`, `weather_precipitation` (opcionales).

El diseño (tokens de color/tipografía/espaciado) está documentado en
`DESIGN.md` en la raíz de `web/`.
