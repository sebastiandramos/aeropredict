import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react'
import type { Factor } from '../lib/types'

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

function sortFactors(factors: Factor[]) {
  const increases = factors
    .filter((f) => f.direction === 'increase' || (f.direction === undefined && (f.contribution ?? 0) > 0))
    .slice()
    .sort((a, b) => Math.abs(b.contribution ?? 0) - Math.abs(a.contribution ?? 0))
    .slice(0, 3)
  const decreases = factors
    .filter((f) => f.direction === 'decrease' || (f.direction === undefined && (f.contribution ?? 0) < 0))
    .slice()
    .sort((a, b) => Math.abs(b.contribution ?? 0) - Math.abs(a.contribution ?? 0))
    .slice(0, 3)
  return { increases, decreases }
}

export default function FactorList({ factors }: FactorListProps) {
  if (factors.length === 0) {
    return null
  }
  const { increases, decreases } = sortFactors(factors)
  const renderGroup = (items: Factor[], title: string) => {
    if (items.length === 0) return null
    return (
      <div className="factors-group">
        <h4 className="factors-group-title">{title}</h4>
        <ul className="factors-list">
          {items.map((f) => {
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
  return (
    <div className="factors">
      <div className="factors-header">
        <h3 className="panel-title">Factores que influyen en la predicción</h3>
      </div>
      <p className="factors-disclaimer">
        Estos factores muestran cómo han influido algunas variables en la probabilidad estimada de retraso. Indican asociaciones aprendidas por el modelo y no demuestran que una variable sea la causa del retraso.
        Las contribuciones están en log-odds, no son porcentajes ni minutos de retraso. La contribución indica la dirección en la que cada variable desplaza la estimación del modelo.
        Los factores mostrados explican la estimación del riesgo de retraso de la primera etapa del modelo. La estimación de minutos corresponde a una segunda etapa de regresión.
      </p>
      {renderGroup(increases, 'Aumentan el riesgo')}
      {renderGroup(decreases, 'Reducen el riesgo')}
    </div>
  )
}
