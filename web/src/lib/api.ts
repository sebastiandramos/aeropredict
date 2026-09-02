// Cliente HTTP compartido para la API de predicción.
// Todas las llamadas pasan por aquí (no hay capas HTTP duplicadas).

import type {
  Alert,
  ApiConfig,
  AuthRequest,
  AuthResponse,
  DelayFeatures,
  DelayResponse,
  EtaRequest,
  EtaResponse,
  HealthResponse,
  Subscription,
  SubscriptionCreateRequest,
} from './types'

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

const DEFAULT_BASE_URL = 'http://localhost:8000'

export function resolveBaseUrl(): string {
  return (import.meta.env.VITE_API_BASE_URL as string | undefined) || DEFAULT_BASE_URL
}

export function isMockMode(): boolean {
  const raw = import.meta.env.VITE_USE_MOCK
  // Default: mock activo salvo que se desactive explícitamente.
  return raw === undefined || raw === '' || raw === 'true' || raw === '1'
}

function buildConfig(): ApiConfig {
  return {
    baseUrl: resolveBaseUrl(),
    apiKey: (import.meta.env.VITE_API_KEY as string | undefined) || undefined,
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const config = buildConfig()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(init.headers as Record<string, string> | undefined),
  }
  if (config.apiKey) {
    headers['X-API-Key'] = config.apiKey
  }

  let res: Response
  try {
    res = await fetch(`${config.baseUrl}${path}`, { ...init, headers })
  } catch {
    throw new ApiError('No se pudo conectar con el servidor.', 0)
  }

  if (!res.ok) {
    throw new ApiError(`Error del servidor (${res.status}).`, res.status)
  }

  return (await res.json()) as T
}

export async function fetchHealth(): Promise<HealthResponse> {
  return request<HealthResponse>('/health')
}

export async function predictDelay(features: DelayFeatures): Promise<DelayResponse> {
  return request<DelayResponse>('/predict/delay', {
    method: 'POST',
    body: JSON.stringify(features),
  })
}

export async function predictEta(req: EtaRequest): Promise<EtaResponse> {
  return request<EtaResponse>('/predict/eta', {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

export async function register(req: AuthRequest): Promise<AuthResponse> {
  return request<AuthResponse>('/auth/register', {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

export async function login(req: AuthRequest): Promise<AuthResponse> {
  return request<AuthResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

// ===================================================================
// Suscripciones y alertas — "mis vuelos" (auth-scoped, Bearer token)
// ===================================================================
// El token se lee de localStorage (clave `aeropredict.token`, la misma que
// usa App.tsx para la sesión) y se envía como `Authorization: Bearer <token>`.

const TOKEN_KEY = 'aeropredict.token'

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem(TOKEN_KEY)
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export async function getSubscriptions(): Promise<Subscription[]> {
  return request<Subscription[]>('/alerts/subscriptions', {
    headers: authHeaders(),
  })
}

export async function createSubscription(
  req: SubscriptionCreateRequest,
): Promise<Subscription> {
  return request<Subscription>('/alerts/subscriptions', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(req),
  })
}

export async function deleteSubscription(flightKey: string): Promise<void> {
  // El backend responde 204 No Content (sin cuerpo) en este endpoint, así que
  // no podemos usar `request<T>` (que hace `res.json()` y lanzaría un
  // SyntaxError sobre un cuerpo vacío). Hacemos el fetch directamente,
  // reutilizando la misma construcción de headers (X-API-Key + Bearer).
  const config = buildConfig()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...authHeaders(),
  }
  if (config.apiKey) {
    headers['X-API-Key'] = config.apiKey
  }

  let res: Response
  try {
    res = await fetch(
      `${config.baseUrl}/alerts/subscriptions/${encodeURIComponent(flightKey)}`,
      { method: 'DELETE', headers },
    )
  } catch {
    throw new ApiError('No se pudo conectar con el servidor.', 0)
  }

  if (!res.ok) {
    throw new ApiError(`Error del servidor (${res.status}).`, res.status)
  }
}

export async function getAlerts(read?: boolean): Promise<Alert[]> {
  const query = read === undefined ? '' : `?read=${read}`
  return request<Alert[]>(`/alerts${query}`, {
    headers: authHeaders(),
  })
}

export async function markAlertRead(alertId: number): Promise<Alert> {
  return request<Alert>(`/alerts/${alertId}/read`, {
    method: 'PATCH',
    headers: authHeaders(),
  })
}
