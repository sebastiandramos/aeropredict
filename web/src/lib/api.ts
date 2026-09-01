// Cliente HTTP compartido para la API de predicción.
// Todas las llamadas pasan por aquí (no hay capas HTTP duplicadas).

import type {
  ApiConfig,
  AuthRequest,
  AuthResponse,
  DelayFeatures,
  DelayResponse,
  EtaRequest,
  EtaResponse,
  HealthResponse,
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
