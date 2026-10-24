// Tipos del contrato de la API de predicción (FastAPI).

export interface HealthResponse {
  status: string
  model_version: string | null
}

export interface DelayFeatures {
  hour_of_day: number
  day_of_week: number
  airline: string
  route_distance: number
  aircraft_type?: string
  aircraft_manufacturer?: string
  aircraft_operator?: string
  weather_temperature_2m?: number | null
  weather_precipitation?: number | null
  // Predictores del modelo de dos etapas (todos opcionales: el backend los
  // deriva o imputa con las medianas del entrenamiento).
  origin_iata?: string | null
  destination_iata?: string | null
  other_airport_iata?: string | null
  dew_point_c?: number | null
  relative_humidity?: number | null
}

/**
 * Factor explicativo devuelto por la API: contribución LOCAL de una variable
 * al log-odds de la etapa de clasificación, calculada para esta predicción.
 * `direction: 'increase'` significa que empuja la probabilidad de retraso
 * hacia arriba. No es un porcentaje ni minutos de retraso.
 */
export type FactorDirection = 'increase' | 'decrease'

export interface DelayFactor {
  id: string
  title: string
  /** Valor observado de la variable, ya formateado por el backend. */
  value?: string | null
  /** Contribución en log-odds (positiva = aumenta el riesgo). */
  contribution?: number | null
  direction?: FactorDirection | null
  /** Semántica antigua del wire: positive = aumenta el riesgo. */
  impact?: 'positive' | 'negative' | string
  description?: string | null
}

export interface DelayResponse {
  // Contrato clásico (siempre presente).
  predicted_delay_minutes: number
  confidence: number
  model_version: string | null
  // Contrato del modelo de dos etapas (presente con el artefacto local).
  prob_retraso?: number | null
  prediccion_retraso_15min?: boolean | null
  minutos_retraso_estimados?: number | null
  umbral_aplicado?: number | null
  factores?: DelayFactor[]
  imputaciones?: string[]
  model_source?: string | null
}

export interface EtaRequest {
  scheduled_arrival: string // ISO8601
  features: DelayFeatures
}

export interface EtaResponse {
  estimated_arrival_time: string // ISO8601
  confidence: number
  delay_component: number
  disruption_likely: boolean
  prob_retraso?: number | null
  umbral_aplicado?: number | null
  model_source?: string | null
}

export interface ApiConfig {
  baseUrl: string
  apiKey?: string
}

export type ConnectionStatus = 'checking' | 'connected' | 'demo' | 'error'

// ===================================================================
// Auth — registro y login (email + contraseña + JWT)
// ===================================================================

export interface AuthRequest {
  email: string
  password: string
}

export interface AuthResponse {
  token: string
  user_id: string
  email: string
  expires_in: number
}

/** Sesión persistida en localStorage (token + datos del usuario). */
export interface Session {
  token: string
  userId: string
  email: string
}

// ===================================================================
// Suscripciones y alertas — "mis vuelos" (auth-scoped)
// ===================================================================
// Los nombres de campo siguen snake_case para reflejar EXACTAMENTE el
// contrato del backend (src/aeropredict/api/models.py), igual que el resto
// de tipos de este archivo (user_id, predicted_delay_minutes, …).

export interface SubscriptionCreateRequest {
  flight_key: string
  flight_number: string
  from_airport: string
  to_airport: string
  schedule_local: string
  threshold_minutes: number
  email?: string | null
}

export interface Subscription {
  user_id: string
  flight_key: string
  flight_number: string
  from_airport: string
  to_airport: string
  schedule_local: string
  threshold_minutes: number
  email?: string | null
  created_at: string
  updated_at: string
}

export interface Alert {
  id: number
  user_id: string
  flight_key: string
  severity: string
  delay_minutes_predicted?: number | null
  factor_jsonb?: Record<string, unknown> | null
  email_sent: boolean
  read: boolean
  created_at: string
}
