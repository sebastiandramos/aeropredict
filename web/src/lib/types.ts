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
}

export interface DelayResponse {
  predicted_delay_minutes: number
  confidence: number
  model_version: string | null
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
