import { useEffect, useState } from 'react'
import Header from './components/Header'
import FlightForm from './components/FlightForm'
import ResultPanel, { type ResultState } from './components/ResultPanel'
import { checkHealth, runPrediction } from './lib/service'
import type { ConnectionStatus, DelayFeatures } from './lib/types'
import './App.css'

export default function App() {
  const [status, setStatus] = useState<ConnectionStatus>('checking')
  const [modelVersion, setModelVersion] = useState<string | null>(null)
  const [result, setResult] = useState<ResultState>({ kind: 'empty' })

  useEffect(() => {
    let cancelled = false
    checkHealth().then(({ status: s, modelVersion: mv }) => {
      if (!cancelled) {
        setStatus(s)
        setModelVersion(mv)
      }
    })
    return () => {
      cancelled = true
    }
  }, [])

  async function handleSubmit(features: DelayFeatures, scheduledArrival: string) {
    setResult({ kind: 'loading' })
    try {
      const prediction = await runPrediction(features, scheduledArrival)
      setResult({ kind: 'success', result: prediction })
    } catch (err) {
      const message =
        err instanceof Error ? err.message : 'Error inesperado al predecir.'
      setResult({ kind: 'error', message })
    }
  }

  return (
    <div className="app">
      <Header status={status} modelVersion={modelVersion} />

      <main className="main">
        <div className="hero">
          <h1 className="hero-title">Predicción de retrasos de vuelos</h1>
          <p className="hero-subtitle">
            Introduce los datos de tu vuelo y obtén una estimación del retraso
            previsto, la hora de llegada y los factores que influyen en el
            resultado.
          </p>
        </div>

        <div className="layout">
          <section className="panel form-panel" aria-label="Datos del vuelo">
            <h2 className="panel-title">Datos del vuelo</h2>
            <FlightForm onSubmit={handleSubmit} loading={result.kind === 'loading'} />
          </section>

          <ResultPanel state={result} />
        </div>
      </main>

      <footer className="footer">
        <p>AeroPredict · Trabajo Fin de Máster · Datos simulados en modo demo</p>
      </footer>
    </div>
  )
}
