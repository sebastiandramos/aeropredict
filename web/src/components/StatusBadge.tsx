import type { ConnectionStatus } from '../lib/types'

interface StatusBadgeProps {
  status: ConnectionStatus
  modelVersion: string | null
}

const LABELS: Record<ConnectionStatus, string> = {
  checking: 'Comprobando conexión…',
  connected: 'API conectada',
  demo: 'Demo (datos simulados)',
  error: 'API no disponible',
}

export default function StatusBadge({ status, modelVersion }: StatusBadgeProps) {
  const label = LABELS[status]
  const isConnected = status === 'connected'
  const isDemo = status === 'demo'

  return (
    <div
      className={`status-badge ${isConnected ? 'status-connected' : ''} ${isDemo ? 'status-demo' : ''}`}
      role="status"
      aria-live="polite"
    >
      <span className="status-dot" aria-hidden="true" />
      <span className="status-label">{label}</span>
      {modelVersion && (
        <span className="status-version tabular">Modelo v{modelVersion}</span>
      )}
    </div>
  )
}
