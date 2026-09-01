// Respuestas mock deterministas para que la app funcione sin backend.
// Se usan cuando VITE_USE_MOCK=true (por defecto) o cuando /health no responde.

import type {
  DelayFeatures,
  DelayResponse,
  EtaResponse,
  HealthResponse,
} from './types'

export const MOCK_MODEL_VERSION = '0.1.0'

export function mockHealth(): HealthResponse {
  return { status: 'ok', model_version: MOCK_MODEL_VERSION }
}

// Hash determinista a partir de una cadena (para variar el resultado por ruta).
function hashString(input: string): number {
  let h = 0
  for (let i = 0; i < input.length; i++) {
    h = (h << 5) - h + input.charCodeAt(i)
    h |= 0
  }
  return Math.abs(h)
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

/**
 * Genera un retraso simulado determinista a partir de las features.
 * Reglas heurísticas (indicativas, no del modelo real):
 *  - Hora punta (7-9, 18-20) suma retraso.
 *  - Fin de semana (day_of_week 5/6) suma algo.
 *  - Rutas largas (> 1500 km) suman algo.
 *  - Precipitación alta suma retraso.
 */
export function mockDelay(features: DelayFeatures): DelayResponse {
  const seed = hashString(
    `${features.airline}|${features.route_distance}|${features.hour_of_day}|${features.day_of_week}`,
  )

  let minutes = 8 + (seed % 45)

  const rushHour = features.hour_of_day >= 7 && features.hour_of_day <= 9
  const evening = features.hour_of_day >= 18 && features.hour_of_day <= 20
  if (rushHour || evening) minutes += 12

  if (features.day_of_week === 5 || features.day_of_week === 6) minutes += 8

  if (features.route_distance > 1500) minutes += 10
  else if (features.route_distance > 800) minutes += 5

  if (features.weather_precipitation && features.weather_precipitation > 2) {
    minutes += 15
  }

  minutes = clamp(Math.round(minutes), 0, 180)

  return {
    predicted_delay_minutes: minutes,
    confidence: 0.85,
    model_version: MOCK_MODEL_VERSION,
  }
}

/**
 * Genera una ETA simulada determinista. scheduled_arrival es ISO8601.
 */
export function mockEta(
  scheduledArrival: string,
  features: DelayFeatures,
): EtaResponse {
  const delay = mockDelay(features)
  const arrival = new Date(scheduledArrival)
  const estimated = new Date(arrival.getTime() + delay.predicted_delay_minutes * 60000)

  return {
    estimated_arrival_time: estimated.toISOString(),
    confidence: 0.85,
    delay_component: delay.predicted_delay_minutes,
    disruption_likely: delay.predicted_delay_minutes > 45,
  }
}
