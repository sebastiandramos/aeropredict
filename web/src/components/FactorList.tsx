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
  positive: 'Aumenta el riesgo',
  negative: 'Reduce el riesgo',
  neutral: 'Impacto neutro',
} as const

export default function FactorList({ factors }: FactorListProps) {
  if (factors.length === 0) {
    return null
  }
  return (
    <div className="factors">
      <div className="factors-header">
        <h3 className="panel-title">Factores que influyen en la predicción</h3>
      </div>
      <p className="factors-disclaimer">
        Explicación local de la etapa de clasificación (regresión logística).
        Las contribuciones están en log-odds, no son porcentajes ni minutos de
        retraso. Indican asociaciones aprendidas por el modelo, no causas
        demostradas. La estimación de minutos corresponde a la segunda etapa de
        regresión.
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
                  <span className="factor-title">
                    {f.title}
                    {f.value ? ` · ${f.value}` : ''}
                  </span>
                  <span className={`factor-impact factor-impact-${f.impact}`}>
                    {IMPACT_LABEL[f.impact]}
                  </span>
                </div>
                <p className="factor-desc">{f.description}</p>
                {typeof f.contribution === 'number' && (
                  <p className="factor-meta tabular">
                    Contribución: {f.contribution >= 0 ? '+' : ''}
                    {f.contribution.toFixed(2)} al log-odds
                  </p>
                )}
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
