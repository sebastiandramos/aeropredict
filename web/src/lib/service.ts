// Capa de servicio: decide entre API real y mock según configuración.
// También deriva los "factores explicativos" indicativos a partir del formulario.

import { fetchHealth, isMockMode, login as apiLogin, predictDelay, predictEta, register as apiRegister } from './api'
import {
  createSubscription as apiCreateSubscription,
  deleteSubscription as apiDeleteSubscription,
  getAlerts as apiGetAlerts,
  getSubscriptions as apiGetSubscriptions,
  markAlertRead as apiMarkAlertRead,
} from './api'
import {
  mockAuth,
  mockCreateSubscription,
  mockDelay,
  mockDeleteSubscription,
  mockEta,
  mockGetAlerts,
  mockGetSubscriptions,
  mockHealth,
  mockMarkAlertRead,
} from './mock'
import type {
  Alert,
  AuthRequest,
  AuthResponse,
  ConnectionStatus,
  DelayFactor,
  DelayFeatures,
  DelayResponse,
  EtaResponse,
  HealthResponse,
  Subscription,
  SubscriptionCreateRequest,
} from './types'

export interface Factor {
  id: string
  title: string
  description: string
  value?: string
  contribution?: number
  direction?: 'increase' | 'decrease'
  impact: 'positive' | 'negative' | 'neutral'
}

export interface PredictionResult {
  delay: DelayResponse
  eta: EtaResponse
  factors: Factor[]
}

export async function checkHealth(): Promise<{
  status: ConnectionStatus
  modelVersion: string | null
}> {
  if (isMockMode()) {
    return { status: 'demo', modelVersion: mockHealth().model_version }
  }

  try {
    const health: HealthResponse = await fetchHealth()
    if (health.status === 'ok') {
      return { status: 'connected', modelVersion: health.model_version }
    }
    return { status: 'error', modelVersion: health.model_version }
  } catch {
    // Si la API no responde, caemos a demo para que la app siga funcionando.
    return { status: 'demo', modelVersion: mockHealth().model_version }
  }
}

export async function runPrediction(
  features: DelayFeatures,
  scheduledArrival: string,
): Promise<PredictionResult> {
  if (isMockMode()) {
    return {
      delay: mockDelay(features),
      eta: mockEta(scheduledArrival, features),
      factors: deriveFactors(features),
    }
  }

  try {
    const [delay, eta] = await Promise.all([
      predictDelay(features),
      predictEta({ scheduled_arrival: scheduledArrival, features }),
    ])
    return { delay, eta, factors: factorsFromResponse(delay, features) }
  } catch {
    // Fallback a mock si la API falla en tiempo de ejecución.
    return {
      delay: mockDelay(features),
      eta: mockEta(scheduledArrival, features),
      factors: deriveFactors(features),
    }
  }
}

/**
 * Usa los factores reales del modelo cuando la API los devuelve y, si no
 * (modelo legacy o modo demo), cae al desglose heurístico del formulario.
 *
 * Los factores del artefacto local son una explicación LOCAL de la etapa de
 * clasificación (contribución en log-odds para este vuelo). Se muestran tal
 * como los calcula el modelo: ``direction: 'increase'`` aumenta el riesgo de
 * retraso, ``decrease`` lo reduce. No se invierte la semántica aquí para
 * mantener la coherencia con la respuesta de la API.
 */
function formatLogOdds(contribution: number): string {
  if (!Number.isFinite(contribution)) {
    return ''
  }
  const sign = contribution >= 0 ? '+' : ''
  return `${sign}${contribution.toFixed(2)}`
}

function factorsFromResponse(delay: DelayResponse, features: DelayFeatures): Factor[] {
  const apiFactors = (delay.factores ?? []) as DelayFactor[]
  if (apiFactors.length === 0) {
    return deriveFactors(features)
  }
  return apiFactors
    .map((factor) => {
      const direction =
        factor.direction ??
        (factor.impact === 'positive' ? 'increase' : factor.impact === 'negative' ? 'decrease' : undefined)
      if (!direction) {
        return null
      }
      const contribution = typeof factor.contribution === 'number' ? factor.contribution : undefined
      const value = factor.value ? factor.value : undefined
      const parts = [factor.title + (value ? ` ${value}` : '')]
      if (typeof contribution === 'number') {
        parts.push(`contribución ${formatLogOdds(contribution)} al log-odds`)
      }
      parts.push(direction === 'increase' ? 'Aumenta el riesgo' : 'Reduce el riesgo')
      return {
        id: factor.id,
        title: factor.title,
        description: factor.description || parts.join(' · '),
        value,
        contribution,
        direction,
        impact: direction === 'increase' ? 'negative' : 'positive',
      } satisfies Factor
    })
    .filter((f) => f !== null) as unknown as Factor[]
}

/**
 * Registro de usuario. En modo mock devuelve una sesión sintética; en modo
 * real llama a POST /auth/register y propaga los errores (409 email duplicado,
 * 422 validación) para que la UI los muestre.
 */
export async function register(req: AuthRequest): Promise<AuthResponse> {
  if (isMockMode()) {
    return mockAuth(req)
  }
  return apiRegister(req)
}

/**
 * Login de usuario. En modo mock devuelve una sesión sintética; en modo real
 * llama a POST /auth/login y propaga los errores (401 credenciales inválidas).
 */
export async function login(req: AuthRequest): Promise<AuthResponse> {
  if (isMockMode()) {
    return mockAuth(req)
  }
  return apiLogin(req)
}

// ===================================================================
// Suscripciones y alertas — "mis vuelos"
// ===================================================================
// En modo mock se usan las implementaciones deterministas en memoria; en modo
// real se llama a la API y se propagan los errores (401 sesión caducada, 404
// no encontrado) para que la UI los distinga.

export async function getSubscriptions(): Promise<Subscription[]> {
  if (isMockMode()) {
    return mockGetSubscriptions()
  }
  return apiGetSubscriptions()
}

export async function createSubscription(
  req: SubscriptionCreateRequest,
): Promise<Subscription> {
  if (isMockMode()) {
    return mockCreateSubscription(req)
  }
  return apiCreateSubscription(req)
}

export async function deleteSubscription(flightKey: string): Promise<void> {
  if (isMockMode()) {
    mockDeleteSubscription(flightKey)
    return
  }
  await apiDeleteSubscription(flightKey)
}

export async function getAlerts(read?: boolean): Promise<Alert[]> {
  if (isMockMode()) {
    return mockGetAlerts(read)
  }
  return apiGetAlerts(read)
}

export async function markAlertRead(alertId: number): Promise<Alert> {
  if (isMockMode()) {
    return mockMarkAlertRead(alertId)
  }
  return apiMarkAlertRead(alertId)
}

const DAY_NAMES = [
  'lunes',
  'martes',
  'miércoles',
  'jueves',
  'viernes',
  'sábado',
  'domingo',
]

/**
 * Deriva un desglose legible de los factores que más influyen en el resultado.
 * La API no devuelve importancia de features, así que esto es indicativo y se
 * etiqueta como tal en la UI.
 */
export function deriveFactors(features: DelayFeatures): Factor[] {
  const factors: Factor[] = []

  // Efecto de fin de semana
  if (features.day_of_week === 5 || features.day_of_week === 6) {
    factors.push({
      id: 'weekend',
      title: 'Fin de semana',
      description: `Salida en ${DAY_NAMES[features.day_of_week]}: mayor densidad de tráfico aéreo.`,
      impact: 'negative',
    })
  } else {
    factors.push({
      id: 'weekday',
      title: 'Día laborable',
      description: `Salida en ${DAY_NAMES[features.day_of_week]}: tráfico más regular.`,
      impact: 'positive',
    })
  }

  // Banda horaria
  const rushHour = features.hour_of_day >= 7 && features.hour_of_day <= 9
  const evening = features.hour_of_day >= 18 && features.hour_of_day <= 20
  if (rushHour || evening) {
    factors.push({
      id: 'hour',
      title: 'Hora punta',
      description: `Salida a las ${String(features.hour_of_day).padStart(2, '0')}:00 h, franja de alta congestión.`,
      impact: 'negative',
    })
  } else {
    factors.push({
      id: 'hour',
      title: 'Franja horaria',
      description: `Salida a las ${String(features.hour_of_day).padStart(2, '0')}:00 h, fuera de las horas punta.`,
      impact: 'positive',
    })
  }

  // Banda de distancia
  if (features.route_distance > 1500) {
    factors.push({
      id: 'distance',
      title: 'Ruta larga',
      description: `${Math.round(features.route_distance)} km: mayor exposición a incidencias en ruta.`,
      impact: 'negative',
    })
  } else if (features.route_distance > 800) {
    factors.push({
      id: 'distance',
      title: 'Ruta media',
      description: `${Math.round(features.route_distance)} km: duración intermedia.`,
      impact: 'neutral',
    })
  } else {
    factors.push({
      id: 'distance',
      title: 'Ruta corta',
      description: `${Math.round(features.route_distance)} km: menor exposición a incidencias.`,
      impact: 'positive',
    })
  }

  // Meteorología
  if (features.weather_precipitation && features.weather_precipitation > 2) {
    factors.push({
      id: 'weather',
      title: 'Precipitación',
      description: `${features.weather_precipitation.toFixed(1)} mm: condiciones meteorológicas adversas.`,
      impact: 'negative',
    })
  } else if (features.weather_temperature_2m != null) {
    factors.push({
      id: 'weather',
      title: 'Temperatura',
      description: `${features.weather_temperature_2m.toFixed(1)} °C: condiciones meteorológicas estables.`,
      impact: 'positive',
    })
  }

  return factors
}
