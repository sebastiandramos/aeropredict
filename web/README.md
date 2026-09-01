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
| POST | `/auth/register` | `{email, password}` | `201 → {token, user_id, email, expires_in}` · `409` email ya registrado · `422` validación |
| POST | `/auth/login` | `{email, password}` | `200 → {token, user_id, email, expires_in}` · `401` credenciales inválidas |
| GET | `/alerts/subscriptions` | — (Bearer) | `200 → Subscription[]` · `401` sin token válido |
| POST | `/alerts/subscriptions` | `SubscriptionCreateRequest` (Bearer) | `201 → Subscription` · `401` · `422` validación |
| DELETE | `/alerts/subscriptions/{flight_key}` | — (Bearer) | `204` · `401` · `404` no existe |
| GET | `/alerts` | `?read=` opcional (Bearer) | `200 → Alert[]` · `401` |
| PATCH | `/alerts/{alert_id}/read` | — (Bearer) | `200 → Alert` · `401` · `404` no es tuya |

`DelayFeatures`: `hour_of_day`, `day_of_week`, `airline`, `route_distance`
(obligatorios); `aircraft_type`, `aircraft_manufacturer`, `aircraft_operator`,
`weather_temperature_2m`, `weather_precipitation` (opcionales).

`SubscriptionCreateRequest`: `flight_key`, `flight_number`, `from_airport`,
`to_airport`, `schedule_local`, `threshold_minutes` (default 60), `email`
(opcional). `Subscription` añade `user_id`, `created_at`, `updated_at`.
`Alert`: `id`, `user_id`, `flight_key`, `severity`, `delay_minutes_predicted`,
`factor_jsonb`, `email_sent`, `read`, `created_at`.

Los endpoints de suscripciones y alertas exigen un token Bearer (el mismo JWT
de la sesión, clave `aeropredict.token`). En modo demo (`VITE_USE_MOCK`) se
usan implementaciones deterministas en memoria sembradas con una suscripción y
una alerta de ejemplo para que la vista «Mis vuelos» no esté vacía.

## Sesión

La app gestiona la sesión con un JWT en memoria y en `localStorage`
(claves `aeropredict.token` y `aeropredict.user`). Al iniciar sesión o
registrarse, el token se guarda y la vista cambia al predictor; el header
muestra el email del usuario y un botón para cerrar sesión (que limpia el
almacenamiento). En modo demo (`VITE_USE_MOCK`, por defecto) el login/registro
devuelve un token sintético determinista (`mock-token-<email>`) para que el
flujo funcione sin backend.

El diseño (tokens de color/tipografía/espaciado) está documentado en
`DESIGN.md` en la raíz de `web/`.
