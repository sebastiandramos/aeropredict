import { AlertTriangle, CalendarClock, Clock, Loader2, Plane } from 'lucide-react'
import type { PredictionResult, FlightSummary } from '../lib/types'
import { AIRPORTS } from '../data/airports'
import FactorList from './FactorList'

export type ResultState =
  | { kind: 'empty' }
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'success'; result: PredictionResult; flightSummary: FlightSummary }

interface ResultPanelProps {
  state: ResultState
}

type Severity = 'ontime' | 'moderate' | 'severe'

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

function formatTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit', hour12: false })
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString('es-ES', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  })
}

function EmptyState() {
  return (
    <div className="result-empty">
      <Plane size={28} aria-hidden="true" />
      <p className="result-empty-title">Sin predicción todavía</p>
      <p className="result-empty-desc">
        Introduce los datos del vuelo y pulsa «Predecir retraso» para ver el
        resultado.
      </p>
    </div>
  )
}

function LoadingState() {
  return (
    <div className="result-loading" role="status" aria-live="polite">
      <Loader2 size={28} className="spin" aria-hidden="true" />
      <p className="result-empty-title">Calculando predicción…</p>
      <p className="result-empty-desc">Consultando el modelo de retrasos.</p>
    </div>
  )
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="result-error" role="alert">
      <AlertTriangle size={28} aria-hidden="true" />
      <p className="result-empty-title">No se pudo completar la predicción</p>
      <p className="result-empty-desc">{message}</p>
    </div>
  )
}

function SuccessState({ result, flightSummary }: { result: PredictionResult; flightSummary: FlightSummary }) {
  const { delay, eta } = result
  const severity = severityFor(delay.predicted_delay_minutes)
  const isOnTime = severity === 'ontime'

  const originAirport = AIRPORTS.find(a => a.icao === flightSummary.originIata)
  const destAirport = AIRPORTS.find(a => a.icao === flightSummary.destinationIata)
  const originCity = originAirport ? originAirport.city : flightSummary.originIata
  const destCity = destAirport ? destAirport.city : flightSummary.destinationIata

  const departureDateObj = new Date(`${flightSummary.departureDate}T${flightSummary.departureTime}`)
  const formattedDate = Number.isNaN(departureDateObj.getTime())
    ? `${flightSummary.departureDate} ${flightSummary.departureTime}`
    : departureDateObj.toLocaleDateString('es-ES', { day: 'numeric', month: 'short', year: 'numeric' })
  const formattedTime = Number.isNaN(departureDateObj.getTime())
    ? flightSummary.departureTime
    : departureDateObj.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit', hour12: false })

  return (
    <div className="result-success">
      <div className="flight-summary-header">
        <span className="flight-summary-route">{originCity} → {destCity}</span>
        <span className="flight-summary-meta">· {flightSummary.airline} · {formattedDate} · Salida {formattedTime} (hora local del aeropuerto de origen)</span>
      </div>
      <div className="result-hero">
        <div className={`severity severity-${severity}`} aria-label={`Alerta del modelo: ${SEVERITY_LABEL[severity]}`}>
          <span className="severity-dot" aria-hidden="true" />
          <span className="severity-label">{SEVERITY_LABEL[severity]}</span>
        </div>
        <div className={`origin-badge origin-${result.origin}`} aria-label={`Origen de la predicción: ${result.origin === 'service' ? 'Servicio' : 'Demo'}`}>
          {result.origin === 'service' ? 'Servicio' : 'Demo'}
        </div>

        <div className="delay-metric">
          <span className="delay-label">Duración estimada si se produce la demora:</span>
          <span className="delay-value tabular">
            {isOnTime ? '0' : Math.round(delay.minutos_retraso_estimados ?? delay.predicted_delay_minutes)}
          </span>
          <span className="delay-unit">min</span>
        </div>

        <div className="delay-confidence">
          Probabilidad estimada de demora ≥15 min:{' '}
          <strong className="tabular">
            {typeof delay.prob_retraso === 'number' ? Math.round(delay.prob_retraso * 100) : '—'}%
          </strong>
          {typeof delay.umbral_aplicado === 'number' && (
            <span className="delay-confidence-sub">
              {' '}
              · umbral {Math.round(delay.umbral_aplicado * 100)}%
            </span>
          )}
        </div>
      </div>

      <div className="result-metrics">
        <div className="metric-card">
          <CalendarClock size={18} aria-hidden="true" />
          <div className="metric-body">
            <span className="metric-label">Hora estimada de llegada</span>
            <span className="metric-value tabular">{formatTime(eta.estimated_arrival_time)}</span>
            <span className="metric-sub">{formatDate(eta.estimated_arrival_time)}</span>
          </div>
        </div>

        <div className="metric-card">
          <Clock size={18} aria-hidden="true" />
          <div className="metric-body">
            <span className="metric-label">Componente de retraso</span>
            <span className="metric-value tabular">
              {Math.round(eta.delay_component)} min
            </span>
            <span className="metric-sub">Sobre la hora programada</span>
          </div>
        </div>
      </div>

      {eta.disruption_likely && (
        <div className="disruption-badge" role="status" aria-label="Alta probabilidad de disrupción">
          <AlertTriangle size={16} aria-hidden="true" />
          Alta probabilidad de disrupción
        </div>
      )}

      <FactorList factors={result.factors} />

      <details className="interpretation-details">
        <summary>Cómo se interpreta</summary>
        <div className="interpretation-content">
          {typeof delay.umbral_aplicado === 'number' && (
            <div>Umbral del modelo: {Math.round(delay.umbral_aplicado * 100)}%</div>
          )}
          {delay.model_version && (
            <div>Modelo: {delay.model_version}</div>
          )}
        </div>
      </details>

      {delay.model_version && (
        <p className="model-version tabular">Modelo: {delay.model_version}</p>
      )}
    </div>
  )
}

export default function ResultPanel({ state }: ResultPanelProps) {
  return (
    <section className="panel result-panel" aria-label="Resultado de la predicción">
      {state.kind === 'empty' && <EmptyState />}
      {state.kind === 'loading' && <LoadingState />}
      {state.kind === 'error' && <ErrorState message={state.message} />}
      {state.kind === 'success' && <SuccessState result={state.result} flightSummary={state.flightSummary} />}
    </section>
  )
}
