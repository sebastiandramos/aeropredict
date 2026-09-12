import { useMemo, useState } from 'react'
import { ChevronDown, Plane, Search } from 'lucide-react'
import { AIRPORTS, COMMON_AIRLINES } from '../data/airports'
import { haversineKm } from '../lib/geo'
import type { DelayFeatures } from '../lib/types'

export interface FlightFormValues {
  origin: string
  destination: string
  airline: string
  departureDate: string // YYYY-MM-DD
  departureTime: string // HH:mm
  aircraftType: string
  weatherTemperature: string
  weatherPrecipitation: string
}

interface FlightFormProps {
  onSubmit: (features: DelayFeatures, scheduledArrival: string) => void
  loading: boolean
}

function toLocalInputValue(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function toLocalTimeValue(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function AirportSelect({
  id,
  label,
  value,
  onChange,
  placeholder,
}: {
  id: string
  label: string
  value: string
  onChange: (icao: string) => void
  placeholder: string
}) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)

  const selected = AIRPORTS.find((a) => a.icao === value)

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return AIRPORTS
    return AIRPORTS.filter(
      (a) =>
        a.icao.toLowerCase().includes(q) ||
        a.name.toLowerCase().includes(q) ||
        a.city.toLowerCase().includes(q) ||
        a.country.toLowerCase().includes(q),
    )
  }, [query])

  function handleSelect(icao: string) {
    onChange(icao)
    setQuery('')
    setOpen(false)
  }

  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <div className="airport-select">
        <div className="airport-trigger">
          <Search size={16} className="airport-search-icon" aria-hidden="true" />
          <input
            id={id}
            type="text"
            role="combobox"
            aria-expanded={open}
            aria-controls={`${id}-listbox`}
            aria-autocomplete="list"
            value={open ? query : selected ? `${selected.icao} — ${selected.name}` : ''}
            placeholder={placeholder}
            onFocus={() => setOpen(true)}
            onChange={(e) => {
              setQuery(e.target.value)
              setOpen(true)
            }}
            onBlur={() => setTimeout(() => setOpen(false), 150)}
            autoComplete="off"
          />
          <ChevronDown size={16} className="airport-chevron" aria-hidden="true" />
        </div>
        {open && (
          <ul className="airport-listbox" id={`${id}-listbox`} role="listbox">
            {filtered.length === 0 && (
              <li className="airport-empty">Sin resultados</li>
            )}
            {filtered.map((a) => (
              <li
                key={a.icao}
                role="option"
                aria-selected={a.icao === value}
                className={`airport-option ${a.icao === value ? 'airport-option-selected' : ''}`}
                onMouseDown={(e) => {
                  e.preventDefault()
                  handleSelect(a.icao)
                }}
              >
                <span className="airport-icao tabular">{a.icao}</span>
                <span className="airport-meta">
                  <span className="airport-name">{a.name}</span>
                  <span className="airport-city">
                    {a.city} · {a.country}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

export default function FlightForm({ onSubmit, loading }: FlightFormProps) {
  const now = new Date()
  const [values, setValues] = useState<FlightFormValues>({
    origin: '',
    destination: '',
    airline: '',
    departureDate: toLocalInputValue(now),
    departureTime: toLocalTimeValue(now),
    aircraftType: '',
    weatherTemperature: '',
    weatherPrecipitation: '',
  })
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const originAirport = AIRPORTS.find((a) => a.icao === values.origin)
  const destAirport = AIRPORTS.find((a) => a.icao === values.destination)

  const routeDistance = useMemo(() => {
    if (originAirport && destAirport) {
      return Math.round(
        haversineKm(
          originAirport.lat,
          originAirport.lon,
          destAirport.lat,
          destAirport.lon,
        ),
      )
    }
    return null
  }, [originAirport, destAirport])

  function set<K extends keyof FlightFormValues>(key: K, val: FlightFormValues[K]) {
    setValues((v) => ({ ...v, [key]: val }))
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    if (!values.origin) {
      setError('Selecciona un aeropuerto de origen.')
      return
    }
    if (!values.destination) {
      setError('Selecciona un aeropuerto de destino.')
      return
    }
    if (values.origin === values.destination) {
      setError('El origen y el destino deben ser distintos.')
      return
    }
    if (!values.airline.trim()) {
      setError('Indica la aerolínea (código IATA, p. ej. IB).')
      return
    }
    if (!values.departureDate || !values.departureTime) {
      setError('Indica la fecha y hora de salida.')
      return
    }
    if (routeDistance === null) {
      setError('No se pudo calcular la distancia de la ruta.')
      return
    }

    const departure = new Date(`${values.departureDate}T${values.departureTime}`)
    if (Number.isNaN(departure.getTime())) {
      setError('Fecha u hora de salida no válida.')
      return
    }

    // Hora estimada de llegada: asumimos ~ velocidad media 800 km/h + 30 min.
    const flightMinutes = Math.round((routeDistance / 800) * 60) + 30
    const scheduledArrival = new Date(departure.getTime() + flightMinutes * 60000)

    const features: DelayFeatures = {
      hour_of_day: departure.getHours(),
      day_of_week: (departure.getDay() + 6) % 7, // 0 = lunes
      airline: values.airline.trim().toUpperCase(),
      route_distance: routeDistance,
      aircraft_type: values.aircraftType.trim() || undefined,
      weather_temperature_2m:
        values.weatherTemperature.trim() === ''
          ? null
          : Number(values.weatherTemperature),
      weather_precipitation:
        values.weatherPrecipitation.trim() === ''
          ? null
          : Number(values.weatherPrecipitation),
    }

    onSubmit(features, scheduledArrival.toISOString())
  }

  return (
    <form className="form" onSubmit={handleSubmit} noValidate>
      <div className="form-grid">
        <AirportSelect
          id="origin"
          label="Origen"
          value={values.origin}
          onChange={(icao) => set('origin', icao)}
          placeholder="Buscar aeropuerto…"
        />
        <AirportSelect
          id="destination"
          label="Destino"
          value={values.destination}
          onChange={(icao) => set('destination', icao)}
          placeholder="Buscar aeropuerto…"
        />
      </div>

      <div className="form-grid">
        <div className="field">
          <label className="field-label" htmlFor="airline">
            Aerolínea
          </label>
          <input
            id="airline"
            className="input"
            type="text"
            list="airlines-list"
            placeholder="Código IATA (IB, VY, FR…)"
            value={values.airline}
            onChange={(e) => set('airline', e.target.value)}
            autoComplete="off"
          />
          <datalist id="airlines-list">
            {COMMON_AIRLINES.map((a) => (
              <option key={a} value={a} />
            ))}
          </datalist>
        </div>

        <div className="field">
          <label className="field-label" htmlFor="departure-date">
            Fecha de salida
          </label>
          <input
            id="departure-date"
            className="input"
            type="date"
            value={values.departureDate}
            onChange={(e) => set('departureDate', e.target.value)}
          />
        </div>

        <div className="field">
          <label className="field-label" htmlFor="departure-time">
            Hora de salida
          </label>
          <input
            id="departure-time"
            className="input"
            type="time"
            value={values.departureTime}
            onChange={(e) => set('departureTime', e.target.value)}
          />
        </div>
      </div>

      <div className="distance-row">
        <Plane size={16} aria-hidden="true" />
        {routeDistance !== null ? (
          <span>
            Distancia estimada:{' '}
            <strong className="tabular">{routeDistance.toLocaleString('es-ES')} km</strong>
          </span>
        ) : (
          <span className="distance-hint">
            Selecciona origen y destino para calcular la distancia.
          </span>
        )}
      </div>

      <button
        type="button"
        className="advanced-toggle"
        onClick={() => setShowAdvanced((s) => !s)}
        aria-expanded={showAdvanced}
      >
        <span>Campos avanzados</span>
        <ChevronDown
          size={16}
          className={`advanced-chevron ${showAdvanced ? 'advanced-chevron-open' : ''}`}
          aria-hidden="true"
        />
      </button>

      {showAdvanced && (
        <div className="advanced-panel">
          <div className="form-grid">
            <div className="field">
              <label className="field-label" htmlFor="aircraft-type">
                Tipo de aeronave
              </label>
              <input
                id="aircraft-type"
                className="input"
                type="text"
                placeholder="Opcional (A320, B738…)"
                value={values.aircraftType}
                onChange={(e) => set('aircraftType', e.target.value)}
                autoComplete="off"
              />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="weather-temp">
                Temperatura (°C)
              </label>
              <input
                id="weather-temp"
                className="input"
                type="number"
                step="0.1"
                placeholder="Opcional"
                value={values.weatherTemperature}
                onChange={(e) => set('weatherTemperature', e.target.value)}
              />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="weather-precip">
                Precipitación (mm)
              </label>
              <input
                id="weather-precip"
                className="input"
                type="number"
                step="0.1"
                min="0"
                placeholder="Opcional"
                value={values.weatherPrecipitation}
                onChange={(e) => set('weatherPrecipitation', e.target.value)}
              />
            </div>
          </div>
        </div>
      )}

      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}

      <button type="submit" className="btn-primary" disabled={loading}>
        {loading ? 'Calculando…' : 'Predecir retraso'}
      </button>
    </form>
  )
}
