import { Plane } from 'lucide-react'
import type { ConnectionStatus } from '../lib/types'
import StatusBadge from './StatusBadge'

interface HeaderProps {
  status: ConnectionStatus
  modelVersion: string | null
}

export default function Header({ status, modelVersion }: HeaderProps) {
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
        <StatusBadge status={status} modelVersion={modelVersion} />
      </div>
    </header>
  )
}
