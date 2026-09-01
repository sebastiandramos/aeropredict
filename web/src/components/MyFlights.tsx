import { useEffect, useMemo, useState } from 'react'
import { Bell, Loader2, Plane, Plus, Trash2 } from 'lucide-react'
import { AIRPORTS, AIRPORT_BY_ICAO } from '../data/airports'
import { haversineKm } from '../lib/geo'
import { createSubscription, deleteSubscription, getAlerts, getSubscriptions, markAlertRead, runPrediction } from '../lib/service'
import type { Alert, DelayFeatures, Subscription } from '../lib/types'

type Severity = 'ontime' | 'moderate' | 'severe'

// Misma lógica de severidad que ResultPanel.tsx (no se exporta desde allí).
function severityFor(minutes: number): Severity {
  if (minutes < 15) return 'ontime'
  if (minutes <= 60) return 'moderate'
  return 'severe'
}

const SEVERITY_LABEL: Record<Severity, string> = {
  ontime: 'Puntual',
  moderate: 'Retraso moderado',
  severe: 'Retraso severo',
}

interface MyFlightsProps {
  onSessionExpired: () => void
}

interface FollowForm {
  origin: string
  destination: string
  flightNumber: string
  scheduleLocal: string
  thresholdMinutes: number
}

function toLocalInputValue(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function toLocalTimeValue(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function formatSchedule(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('es-ES', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatAlertTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('es-ES', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function airportLabel(icao: string): string {
  const a = AIRPORT_BY_ICAO.get(icao)
  return a ? `${a.icao} — ${a.city}` : icao
}

export default function MyFlights({ onSessionExpired }: MyFlightsProps) {
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([])
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showFollow, setShowFollow] = useState(false)

  const now = new Date()
  const [follow, setFollow] = useState<FollowForm>({
    origin: '',
    destination: '',
    flightNumber: '',
    scheduleLocal: `${toLocalInputValue(now)}T${toLocalTimeValue(now)}`,
    thresholdMinutes: 60,
  })
  const [followLoading, setFollowLoading] = useState(false)
  const [followError, setFollowError] = useState<string | null>(null)
  const [followResult, setFollowResult] = useState<{
    delayMinutes: number
    severity: Severity
  } | null>(null)

  const originAirport = AIRPORTS.find((a) => a.icao === follow.origin)
  const destAirport = AIRPORTS.find((a) => a.icao === follow.destination)

  const routeDistance = useMemo(() => {
    if (originAirport && destAirport) {
      return Math.round(
        haversineKm(
          originAirport.lat,
          originAirport.lon,
          destAirport.lat,
          destAirport.lon,
        ),
      )
    }
    return null
  }, [originAirport, destAirport])

  const unreadAlerts = alerts.filter((a) => !a.read)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const [subs, alertList] = await Promise.all([getSubscriptions(), getAlerts()])
      setSubscriptions(subs)
      setAlerts(alertList)
    } catch (err) {
      const status = err instanceof Error && 'status' in err ? (err as { status: number }).status : 0
      if (status === 401) {
        onSessionExpired()
        return
      }
      setError('No se pudieron cargar tus vuelos. Inténtalo de nuevo.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleDelete(flightKey: string) {
    setError(null)
    try {
      await deleteSubscription(flightKey)
      setSubscriptions((subs) => subs.filter((s) => s.flight_key !== flightKey))
    } catch (err) {
      const status = err instanceof Error && 'status' in err ? (err as { status: number }).status : 0
      if (status === 401) {
        onSessionExpired()
        return
      }
      if (status === 404) {
        setSubscriptions((subs) => subs.filter((s) => s.flight_key !== flightKey))
        return
      }
      setError('No se pudo eliminar el vuelo. Inténtalo de nuevo.')
    }
  }

  async function handleMarkRead(alertId: number) {
    try {
      await markAlertRead(alertId)
      setAlerts((list) => list.map((a) => (a.id === alertId ? { ...a, read: true } : a)))
    } catch (err) {
      const status = err instanceof Error && 'status' in err ? (err as { status: number }).status : 0
      if (status === 401) {
        onSessionExpired()
      }
    }
  }

  async function handleFollow(e: React.FormEvent) {
    e.preventDefault()
    setFollowError(null)
    setFollowResult(null)

    if (!follow.origin || !follow.destination) {
      setFollowError('Selecciona origen y destino.')
      return
    }
    if (follow.origin === follow.destination) {
      setFollowError('El origen y el destino deben ser distintos.')
      return
    }
    if (!follow.flightNumber.trim()) {
      setFollowError('Indica el número de vuelo (p. ej. IB1234).')
      return
    }
    if (routeDistance === null) {
      setFollowError('No se pudo calcular la distancia de la ruta.')
      return
    }

    const departure = new Date(follow.scheduleLocal)
    if (Number.isNaN(departure.getTime())) {
      setFollowError('Fecha u hora de salida no válida.')
      return
    }

    setFollowLoading(true)
    try {
      const features: DelayFeatures = {
        hour_of_day: departure.getHours(),
        day_of_week: (departure.getDay() + 6) % 7, // 0 = lunes
        airline: follow.flightNumber.replace(/[0-9]/g, '').toUpperCase() || 'XX',
        route_distance: routeDistance,
      }
      const prediction = await runPrediction(features, departure.toISOString())
      const delayMinutes = prediction.delay.predicted_delay_minutes
      const severity = severityFor(delayMinutes)

      const flightKey = `${follow.flightNumber.trim().toUpperCase()}-${follow.origin}-${follow.destination}`
      await createSubscription({
        flight_key: flightKey,
        flight_number: follow.flightNumber.trim().toUpperCase(),
        from_airport: follow.origin,
        to_airport: follow.destination,
        schedule_local: departure.toISOString(),
        threshold_minutes: follow.thresholdMinutes,
      })

      setFollowResult({ delayMinutes, severity })
      setShowFollow(false)
      setFollow({ ...follow, origin: '', destination: '', flightNumber: '' })
      await load()
    } catch (err) {
      const status = err instanceof Error && 'status' in err ? (err as { status: number }).status : 0
      if (status === 401) {
        onSessionExpired()
        return
      }
      setFollowError('No se pudo seguir el vuelo. Inténtalo de nuevo.')
    } finally {
      setFollowLoading(false)
    }
  }

  return (
    <div className="my-flights">
      <div className="hero">
        <h1 className="hero-title">Mis vuelos</h1>
        <p className="hero-subtitle">
          Sigue los vuelos que te interesan y recibe un aviso cuando el modelo
          predice un retraso por encima de tu umbral.
        </p>
      </div>

      {unreadAlerts.length > 0 && (
        <div className="alerts-banner" role="status" aria-live="polite">
          <Bell size={18} aria-hidden="true" />
          <span>
            Tienes {unreadAlerts.length}{' '}
            {unreadAlerts.length === 1 ? 'alerta nueva' : 'alertas nuevas'} de retraso.
          </span>
        </div>
      )}

      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}

      <div className="my-flights-actions">
        <button
          type="button"
          className="btn-primary"
          onClick={() => {
            setShowFollow((s) => !s)
            setFollowError(null)
            setFollowResult(null)
          }}
          aria-expanded={showFollow}
        >
          <Plus size={18} aria-hidden="true" />
          Seguir vuelo
        </button>
      </div>

      {showFollow && (
        <section className="panel follow-panel" aria-label="Seguir un vuelo">
          <h2 className="panel-title">Seguir un vuelo</h2>
          <form className="form" onSubmit={handleFollow} noValidate>
            <div className="form-grid">
              <div className="field">
                <label className="field-label" htmlFor="follow-origin">
                  Origen
                </label>
                <select
                  id="follow-origin"
                  className="input"
                  value={follow.origin}
                  onChange={(e) => setFollow({ ...follow, origin: e.target.value })}
                >
                  <option value="">Selecciona…</option>
                  {AIRPORTS.map((a) => (
                    <option key={a.icao} value={a.icao}>
                      {a.icao} — {a.city}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label className="field-label" htmlFor="follow-destination">
                  Destino
                </label>
                <select
                  id="follow-destination"
                  className="input"
                  value={follow.destination}
                  onChange={(e) => setFollow({ ...follow, destination: e.target.value })}
                >
                  <option value="">Selecciona…</option>
                  {AIRPORTS.map((a) => (
                    <option key={a.icao} value={a.icao}>
                      {a.icao} — {a.city}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="form-grid">
              <div className="field">
                <label className="field-label" htmlFor="follow-flight-number">
                  Número de vuelo
                </label>
                <input
                  id="follow-flight-number"
                  className="input"
                  type="text"
                  placeholder="IB1234"
                  value={follow.flightNumber}
                  onChange={(e) => setFollow({ ...follow, flightNumber: e.target.value })}
                  autoComplete="off"
                />
              </div>
              <div className="field">
                <label className="field-label" htmlFor="follow-schedule">
                  Fecha y hora de salida
                </label>
                <input
                  id="follow-schedule"
                  className="input"
                  type="datetime-local"
                  value={follow.scheduleLocal}
                  onChange={(e) => setFollow({ ...follow, scheduleLocal: e.target.value })}
                />
              </div>
            </div>

            <div className="field">
              <label className="field-label" htmlFor="follow-threshold">
                Umbral de alerta (minutos de retraso)
              </label>
              <input
                id="follow-threshold"
                className="input"
                type="number"
                min="1"
                step="1"
                value={follow.thresholdMinutes}
                onChange={(e) =>
                  setFollow({ ...follow, thresholdMinutes: Number(e.target.value) })
                }
              />
              <span className="field-hint">
                Recibirás un aviso cuando el retraso predicho supere este umbral.
              </span>
            </div>

            {followError && (
              <p className="form-error" role="alert">
                {followError}
              </p>
            )}

            {followResult && (
              <div className={`follow-result severity severity-${followResult.severity}`}>
                <span className="severity-dot" aria-hidden="true" />
                <span>
                  Retraso previsto:{' '}
                  <strong className="tabular">
                    {Math.round(followResult.delayMinutes)} min
                  </strong>{' '}
                  · {SEVERITY_LABEL[followResult.severity]}
                </span>
              </div>
            )}

            <button type="submit" className="btn-primary" disabled={followLoading}>
              {followLoading ? (
                <Loader2 size={18} className="spin" aria-hidden="true" />
              ) : (
                <Plane size={18} aria-hidden="true" />
              )}
              {followLoading ? 'Prediciendo…' : 'Predecir y seguir'}
            </button>
          </form>
        </section>
      )}

      <section className="panel" aria-label="Tus vuelos seguidos">
        <h2 className="panel-title">Vuelos seguidos</h2>
        {loading ? (
          <div className="result-loading" role="status" aria-live="polite">
            <Loader2 size={28} className="spin" aria-hidden="true" />
            <p className="result-empty-title">Cargando tus vuelos…</p>
          </div>
        ) : subscriptions.length === 0 ? (
          <div className="result-empty">
            <Plane size={28} aria-hidden="true" />
            <p className="result-empty-title">Todavía no sigues ningún vuelo</p>
            <p className="result-empty-desc">
              Pulsa «Seguir vuelo» para añadir el primero y recibir alertas de
              retraso.
            </p>
          </div>
        ) : (
          <ul className="subscription-list">
            {subscriptions.map((sub) => (
              <li key={sub.flight_key} className="subscription-item">
                <div className="subscription-main">
                  <span className="subscription-flight tabular">
                    {sub.flight_number}
                  </span>
                  <span className="subscription-route">
                    {airportLabel(sub.from_airport)} → {airportLabel(sub.to_airport)}
                  </span>
                  <span className="subscription-meta">
                    {formatSchedule(sub.schedule_local)} · umbral{' '}
                    <strong className="tabular">{sub.threshold_minutes} min</strong>
                  </span>
                </div>
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => void handleDelete(sub.flight_key)}
                  aria-label={`Dejar de seguir ${sub.flight_number}`}
                >
                  <Trash2 size={16} aria-hidden="true" />
                  Dejar de seguir
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="panel" aria-label="Alertas de retraso">
        <h2 className="panel-title">Alertas</h2>
        {alerts.length === 0 ? (
          <div className="result-empty">
            <Bell size={28} aria-hidden="true" />
            <p className="result-empty-title">Sin alertas</p>
            <p className="result-empty-desc">
              Cuando un vuelo seguido supere tu umbral, aparecerá aquí una alerta.
            </p>
          </div>
        ) : (
          <ul className="alert-list">
            {alerts.map((alert) => {
              const severity = severityFor(alert.delay_minutes_predicted ?? 0)
              return (
                <li
                  key={alert.id}
                  className={`alert-item ${alert.read ? 'alert-item-read' : ''}`}
                >
                  <div className={`alert-severity severity severity-${severity}`}>
                    <span className="severity-dot" aria-hidden="true" />
                    <span>{SEVERITY_LABEL[severity]}</span>
                  </div>
                  <div className="alert-body">
                    <span className="alert-flight tabular">{alert.flight_key}</span>
                    <span className="alert-meta">
                      {alert.delay_minutes_predicted != null && (
                        <>
                          Retraso previsto:{' '}
                          <strong className="tabular">
                            {Math.round(alert.delay_minutes_predicted)} min
                          </strong>{' '}
                          ·
                        </>
                      )}{' '}
                      {formatAlertTime(alert.created_at)}
                    </span>
                  </div>
                  {!alert.read && (
                    <button
                      type="button"
                      className="btn-ghost"
                      onClick={() => void handleMarkRead(alert.id)}
                    >
                      Marcar como leída
                    </button>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </section>
    </div>
  )
}
