import { AlertTriangle, CalendarClock, Clock, Loader2, Plane } from 'lucide-react'
import type { PredictionResult } from '../lib/service'
import FactorList from './FactorList'

export type ResultState =
  | { kind: 'empty' }
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'success'; result: PredictionResult }

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
  return d.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' })
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

function SuccessState({ result }: { result: PredictionResult }) {
  const { delay, eta } = result
  const severity = severityFor(delay.predicted_delay_minutes)
  const isOnTime = severity === 'ontime'

  return (
    <div className="result-success">
      <div className="result-hero">
        <div className={`severity severity-${severity}`}>
          <span className="severity-dot" aria-hidden="true" />
          <span className="severity-label">{SEVERITY_LABEL[severity]}</span>
        </div>

        <div className="delay-metric">
          <span className="delay-value tabular">
            {isOnTime ? '0' : Math.round(delay.predicted_delay_minutes)}
          </span>
          <span className="delay-unit">{isOnTime ? 'min de retraso' : 'min de retraso previsto'}</span>
        </div>

        <div className="delay-confidence">
          Confianza del modelo:{' '}
          <strong className="tabular">
            {Math.round(delay.confidence * 100)}%
          </strong>
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
        <div className="disruption-badge" role="status">
          <AlertTriangle size={16} aria-hidden="true" />
          Alta probabilidad de disrupción
        </div>
      )}

      <FactorList factors={result.factors} />

      {delay.model_version && (
        <p className="model-version tabular">Modelo v{delay.model_version}</p>
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
      {state.kind === 'success' && <SuccessState result={state.result} />}
    </section>
  )
}
