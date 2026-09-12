import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react'
import type { Factor } from '../lib/service'

interface FactorListProps {
  factors: Factor[]
}

const IMPACT_ICON = {
  positive: ArrowUpRight,
  negative: ArrowDownRight,
  neutral: Minus,
} as const

const IMPACT_LABEL = {
  positive: 'Favorable',
  negative: 'Desfavorable',
  neutral: 'Neutro',
} as const

export default function FactorList({ factors }: FactorListProps) {
  return (
    <div className="factors">
      <div className="factors-header">
        <h3 className="panel-title">Factores explicativos</h3>
        <span className="factors-note">Indicativo</span>
      </div>
      <p className="factors-disclaimer">
        Desglose orientativo a partir de los datos introducidos. La API no
        devuelve importancia de características.
      </p>
      <ul className="factors-list">
        {factors.map((f) => {
          const Icon = IMPACT_ICON[f.impact]
          return (
            <li key={f.id} className="factor-item">
              <span className={`factor-icon factor-icon-${f.impact}`} aria-hidden="true">
                <Icon size={16} />
              </span>
              <div className="factor-body">
                <div className="factor-title-row">
                  <span className="factor-title">{f.title}</span>
                  <span className={`factor-impact factor-impact-${f.impact}`}>
                    {IMPACT_LABEL[f.impact]}
                  </span>
                </div>
                <p className="factor-desc">{f.description}</p>
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
