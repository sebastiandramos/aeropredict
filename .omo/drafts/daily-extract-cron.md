# Draft: Daily Extract Cron Script

## Requirements (confirmed)
- Script Python para extracción diaria automática de vuelos históricos OpenSky
- Ejecución vía cron 2x/día: 6:00 UTC y 19:00 UTC
- Verificar créditos disponibles antes de extraer
- Consultar Delta Lake para saltar aeropuertos ya cargados
- 5s de delay entre peticiones para rate limiting por minuto
- Manejar 404 silenciosamente (sin datos en rango)

## Technical Decisions
- Script: `src/aeropredict/opensky/daily_extract.py`
- Reutiliza módulos existentes: client, extract_flights, storage, config
- Logging a archivo: `data/logs/daily_extract.log`
- Verificación créditos: GET ligero a `/flights/arrival?airport=LEMD` y leer `X-Rate-Limit-Remaining`
- Umbral mínimo: 2000 créditos (~33 aeropuertos × 2 queries × 30 créditos)
- Si créditos insuficientes: log y exit (no extrae)
- Delay entre requests: 5s
- Días a extraer: desde D-1 hacia atrás hasta agotar créditos
- Crontab: `0 6 * * *` y `0 19 * * *`

## Scope Boundaries
- INCLUDE: Script daily_extract.py, logging, crontab setup
- EXCLUDE: Gold layer (features), tracks extraction, state vectors
- EXCLUDE: Dashboard UI, alerting
