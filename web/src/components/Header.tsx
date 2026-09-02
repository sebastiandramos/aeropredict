import { LogOut, Plane } from 'lucide-react'
import type { ConnectionStatus, Session } from '../lib/types'
import StatusBadge from './StatusBadge'

export type AppView = 'predictor' | 'my-flights'

interface HeaderProps {
  status: ConnectionStatus
  modelVersion: string | null
  session: Session | null
  view: AppView
  onViewChange: (view: AppView) => void
  onLogout: () => void
}

export default function Header({
  status,
  modelVersion,
  session,
  view,
  onViewChange,
  onLogout,
}: HeaderProps) {
  return (
    <header className="header">
      <div className="header-inner">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <Plane size={20} strokeWidth={2} />
          </span>
          <div className="brand-text">
            <span className="brand-name">AeroPredict</span>
            <span className="brand-tagline">Predicción de retrasos de vuelos</span>
          </div>
        </div>
        <div className="header-actions">
          {session && (
            <nav className="nav-tabs" aria-label="Vistas">
              <button
                type="button"
                className={`nav-tab ${view === 'predictor' ? 'nav-tab-active' : ''}`}
                aria-current={view === 'predictor' ? 'page' : undefined}
                onClick={() => onViewChange('predictor')}
              >
                Predictor
              </button>
              <button
                type="button"
                className={`nav-tab ${view === 'my-flights' ? 'nav-tab-active' : ''}`}
                aria-current={view === 'my-flights' ? 'page' : undefined}
                onClick={() => onViewChange('my-flights')}
              >
                Mis vuelos
              </button>
            </nav>
          )}
          {session && (
            <div className="session">
              <span className="session-email">{session.email}</span>
              <button type="button" className="btn-ghost" onClick={onLogout}>
                <LogOut size={16} aria-hidden="true" />
                Salir
              </button>
            </div>
          )}
          <StatusBadge status={status} modelVersion={modelVersion} />
        </div>
      </div>
    </header>
  )
}
