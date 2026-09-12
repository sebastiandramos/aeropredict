import { useState } from 'react'
import { Loader2, LogIn, UserPlus } from 'lucide-react'
import { login, register } from '../lib/service'
import type { AuthResponse } from '../lib/types'

export type AuthMode = 'login' | 'register'

interface AuthFormProps {
  onSuccess: (auth: AuthResponse) => void
}

/** Traduce el status HTTP del backend a un mensaje legible. */
function messageForStatus(status: number, mode: AuthMode): string {
  if (mode === 'login' && status === 401) {
    return 'Credenciales inválidas. Comprueba tu email y contraseña.'
  }
  if (mode === 'register' && status === 409) {
    return 'Ese email ya está registrado. Inicia sesión.'
  }
  if (status === 422) {
    return 'Datos no válidos. Revisa el email y que la contraseña tenga al menos 8 caracteres.'
  }
  return 'No se pudo completar la operación. Inténtalo de nuevo.'
}

export default function AuthForm({ onSuccess }: AuthFormProps) {
  const [mode, setMode] = useState<AuthMode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function switchMode(next: AuthMode) {
    setMode(next)
    setError(null)
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    if (!email.trim() || !password) {
      setError('Introduce tu email y contraseña.')
      return
    }

    setLoading(true)
    try {
      const auth =
        mode === 'login'
          ? await login({ email: email.trim(), password })
          : await register({ email: email.trim(), password })
      onSuccess(auth)
    } catch (err) {
      const status = err instanceof Error && 'status' in err ? (err as { status: number }).status : 0
      setError(messageForStatus(status, mode))
    } finally {
      setLoading(false)
    }
  }

  const isLogin = mode === 'login'

  return (
    <form className="form auth-form" onSubmit={handleSubmit} noValidate>
      <div className="auth-tabs" role="tablist" aria-label="Acceso">
        <button
          type="button"
          role="tab"
          aria-selected={isLogin}
          className={`auth-tab ${isLogin ? 'auth-tab-active' : ''}`}
          onClick={() => switchMode('login')}
        >
          Iniciar sesión
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={!isLogin}
          className={`auth-tab ${!isLogin ? 'auth-tab-active' : ''}`}
          onClick={() => switchMode('register')}
        >
          Crear cuenta
        </button>
      </div>

      <div className="field">
        <label className="field-label" htmlFor="auth-email">
          Email
        </label>
        <input
          id="auth-email"
          className="input"
          type="email"
          autoComplete="email"
          placeholder="tu@email.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          aria-describedby={error ? 'auth-error' : undefined}
        />
      </div>

      <div className="field">
        <label className="field-label" htmlFor="auth-password">
          Contraseña
        </label>
        <input
          id="auth-password"
          className="input"
          type="password"
          autoComplete={isLogin ? 'current-password' : 'new-password'}
          placeholder={isLogin ? 'Tu contraseña' : 'Mínimo 8 caracteres'}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          aria-describedby={error ? 'auth-error' : undefined}
        />
      </div>

      {error && (
        <p className="form-error" id="auth-error" role="alert">
          {error}
        </p>
      )}

      <button type="submit" className="btn-primary" disabled={loading}>
        {loading ? (
          <Loader2 size={18} className="spin" aria-hidden="true" />
        ) : isLogin ? (
          <LogIn size={18} aria-hidden="true" />
        ) : (
          <UserPlus size={18} aria-hidden="true" />
        )}
        {loading
          ? 'Procesando…'
          : isLogin
            ? 'Iniciar sesión'
            : 'Crear cuenta'}
      </button>
    </form>
  )
}
