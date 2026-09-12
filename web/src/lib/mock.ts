// Respuestas mock deterministas para que la app funcione sin backend.
// Se usan cuando VITE_USE_MOCK=true (por defecto) o cuando /health no responde.

import type {
  Alert,
  AuthRequest,
  AuthResponse,
  DelayFeatures,
  DelayResponse,
  EtaResponse,
  HealthResponse,
  Subscription,
  SubscriptionCreateRequest,
} from './types'

export const MOCK_MODEL_VERSION = '0.1.0'

export function mockHealth(): HealthResponse {
  return { status: 'ok', model_version: MOCK_MODEL_VERSION }
}

/**
 * Respuesta mock determinista de auth (registro/login). Devuelve un token
 * sintético estable por email para que la sesión funcione sin backend.
 */
export function mockAuth(req: AuthRequest): AuthResponse {
  return {
    token: `mock-token-${req.email}`,
    user_id: `mock-user-${hashString(req.email)}`,
    email: req.email,
    expires_in: 86400,
  }
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

// ===================================================================
// Suscripciones y alertas — mock determinista en memoria
// ===================================================================
// El store se siembra con una suscripción demo para que la vista "Mis
// vuelos" no esté vacía al entrar. Se persiste en memoria durante la sesión
// (no sobrevive a un refresh, igual que el resto del modo demo).

interface MockStore {
  subscriptions: Subscription[]
  alerts: Alert[]
  nextAlertId: number
}

let store: MockStore | null = null

function getStore(): MockStore {
  if (store) return store
  const now = new Date().toISOString()
  store = {
    subscriptions: [
      {
        user_id: 'mock-user-demo',
        flight_key: 'IB1234-LEMD-LEBL',
        flight_number: 'IB1234',
        from_airport: 'LEMD',
        to_airport: 'LEBL',
        schedule_local: '2026-09-01T08:30:00',
        threshold_minutes: 60,
        email: null,
        created_at: now,
        updated_at: now,
      },
    ],
    alerts: [
      {
        id: 1,
        user_id: 'mock-user-demo',
        flight_key: 'IB1234-LEMD-LEBL',
        severity: 'moderate',
        delay_minutes_predicted: 35,
        factor_jsonb: null,
        email_sent: false,
        read: false,
        created_at: now,
      },
    ],
    nextAlertId: 2,
  }
  return store
}

export function mockGetSubscriptions(): Subscription[] {
  return getStore().subscriptions
}

export function mockCreateSubscription(
  req: SubscriptionCreateRequest,
): Subscription {
  const s = getStore()
  const now = new Date().toISOString()
  const existing = s.subscriptions.find((sub) => sub.flight_key === req.flight_key)
  if (existing) {
    const updated: Subscription = {
      ...existing,
      ...req,
      updated_at: now,
    }
    s.subscriptions = s.subscriptions.map((sub) =>
      sub.flight_key === req.flight_key ? updated : sub,
    )
    return updated
  }
  const created: Subscription = {
    user_id: 'mock-user-demo',
    ...req,
    created_at: now,
    updated_at: now,
  }
  s.subscriptions = [...s.subscriptions, created]
  return created
}

export function mockDeleteSubscription(flightKey: string): void {
  const s = getStore()
  s.subscriptions = s.subscriptions.filter((sub) => sub.flight_key !== flightKey)
}

export function mockGetAlerts(read?: boolean): Alert[] {
  const s = getStore()
  if (read === undefined) return s.alerts
  return s.alerts.filter((a) => a.read === read)
}

export function mockMarkAlertRead(alertId: number): Alert {
  const s = getStore()
  const alert = s.alerts.find((a) => a.id === alertId)
  if (!alert) {
    throw new Error(`Alert ${alertId} not found`)
  }
  const updated: Alert = { ...alert, read: true }
  s.alerts = s.alerts.map((a) => (a.id === alertId ? updated : a))
  return updated
}
