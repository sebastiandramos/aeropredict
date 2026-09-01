import { useEffect, useState } from 'react'
import Header, { type AppView } from './components/Header'
import FlightForm from './components/FlightForm'
import ResultPanel, { type ResultState } from './components/ResultPanel'
import AuthForm from './components/AuthForm'
import MyFlights from './components/MyFlights'
import { checkHealth, runPrediction } from './lib/service'
import type { AuthResponse, ConnectionStatus, DelayFeatures, Session } from './lib/types'
import './App.css'

const TOKEN_KEY = 'aeropredict.token'
const USER_KEY = 'aeropredict.user'

function loadSession(): Session | null {
  try {
    const token = localStorage.getItem(TOKEN_KEY)
    const rawUser = localStorage.getItem(USER_KEY)
    if (!token || !rawUser) return null
    const user = JSON.parse(rawUser) as { userId: string; email: string }
    return { token, userId: user.userId, email: user.email }
  } catch {
    return null
  }
}

function saveSession(auth: AuthResponse) {
  localStorage.setItem(TOKEN_KEY, auth.token)
  localStorage.setItem(USER_KEY, JSON.stringify({ userId: auth.user_id, email: auth.email }))
}

function clearSession() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export default function App() {
  const [status, setStatus] = useState<ConnectionStatus>('checking')
  const [modelVersion, setModelVersion] = useState<string | null>(null)
  const [result, setResult] = useState<ResultState>({ kind: 'empty' })
  const [session, setSession] = useState<Session | null>(() => loadSession())
  const [view, setView] = useState<AppView>('predictor')

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

  function handleAuthSuccess(auth: AuthResponse) {
    saveSession(auth)
    setSession({ token: auth.token, userId: auth.user_id, email: auth.email })
  }

  function handleLogout() {
    clearSession()
    setSession(null)
    setResult({ kind: 'empty' })
    setView('predictor')
  }

  function handleSessionExpired() {
    clearSession()
    setSession(null)
    setResult({ kind: 'empty' })
    setView('predictor')
  }

  return (
    <div className="app">
      <Header
        status={status}
        modelVersion={modelVersion}
        session={session}
        view={view}
        onViewChange={setView}
        onLogout={handleLogout}
      />

      <main className="main">
        {session ? (
          view === 'my-flights' ? (
            <MyFlights onSessionExpired={handleSessionExpired} />
          ) : (
            <>
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
            </>
          )
        ) : (
          <div className="auth-layout">
            <section className="panel auth-panel" aria-label="Acceso a tu cuenta">
              <h2 className="panel-title">Accede a tu cuenta</h2>
              <p className="auth-intro">
                Inicia sesión o crea una cuenta para guardar tus vuelos y recibir
                alertas de retraso.
              </p>
              <AuthForm onSuccess={handleAuthSuccess} />
            </section>
          </div>
        )}
      </main>

      <footer className="footer">
        <p>AeroPredict · Trabajo Fin de Máster · Datos simulados en modo demo</p>
      </footer>
    </div>
  )
}
