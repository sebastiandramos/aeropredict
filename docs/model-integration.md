# Integración del modelo de dos etapas (notebook R → Python)

Este documento describe cómo se ha integrado el modelo `AeroPredict_TFM_Modelado_Final_Git.ipynb` (dos etapas: clasificación `retraso_15_min` con regresión logística + regresión `log1p(retraso_minutos)` con GBM) en la API FastAPI y en el frontend React.

## Artefactos generados

Ejecutar el exportador (requiere Doppler con acceso a PostgreSQL):

```bash
doppler run -- python scripts/export_r_model_to_python.py --from-db
```

Se generan en `models/`:

| Fichero | Descripción |
| --- | --- |
| `aeropredict_modelos_finales_v1.pkl` | Artefacto `joblib` con: `clasificacion` (coeficientes, columnas de diseño con intercepto, umbral, imputación, reglas de categorías/dominios), `regresion` (GBM, columnas alineadas, umbral/floor), `version`, `fecha_corte_datos`, `imputation`. |
| `preprocessor_rules.json` | Reglas legibles (niveles de categorías tras rarefacción, dominios temporales, umbral seleccionado en validación, métricas test). |
| `aeropredict_metricas_clasificacion_final.csv` / `aeropredict_metricas_regresion_final.csv` | Métricas en test. |
| `aeropredict_coeficientes_logistica.csv` | Coeficientes de la etapa 1. |
| `aeropredict_predicciones_clasificacion_test.csv` / `aeropredict_predicciones_regresion_test.csv` | Predicciones en test. |

## Parámetros clave (fidelidad al notebook)

- Umbral de clasificación: **0.08** (seleccionado en validación maximizando F1 para `retraso_15_min`; ver `preprocessor_rules.json`).
- Etapa 1: `glm(family = binomial)` reproducido con `sklearn.linear_model.LogisticRegression(fit_intercept=False, C=np.inf, solver="newton-cholesky")`.
- Etapa 2: `gbm` con `GradientBoostingRegressor(loss="squared_error", learning_rate=0.03, n_estimators=300, max_depth=3, min_samples_leaf=20, subsample=0.5, random_state=2026)`, objetivo `log1p(retraso_minutos)`, solo vuelos retrasados en desarrollo; predicción `max(floor=15, expm1(pred_log))`.
- Explicación de factores (etapa 1): los factores se calculan como contribuciones locales al log-odds (suma = `logit`), ordenados por |contribución| y agrupados por dirección (aumentan/reducen el riesgo). Se muestran top 3 por dirección, se filtran contribuciones < 1e-3. Los valores observados se formatean legiblemente; la semántica es asociativa, no causal. Los minutos corresponden a la etapa 2.
- Rarefacción de categorías: `min_freq=2` → nivel `OTRA` (ordenado al final).
- Sin fuga: reglas aprendidas solo con datos de entrenamiento/desarrollo según split cronológico 60/20/20.
- Tratamiento de dummies: contraste por niveles (sin `drop_first`) con `Intercept`, referencia = primer nivel ordenado; matriz alineada al diseño guardado.
- Imputación por medianas de desarrollo: `temperatura_metar=27.0`, `punto_rocio_metar=11.0`, `relh=41.0`. Se derivan `punto_rocio_metar`/`relh` por Magnus cuando solo se conoce uno de ellos.
- Selección de `other_airport_iata`: aeropuerto distinto al español (AENA: prefijos `LE`/`GC`) o `other_airport_iata` explícito; conversión ICAO→IATA vía `airport_codes`.

## Variables predictoras (etapa 1)

`hora_vuelo`, `dia_semana`, `airline_iata`, `other_airport_iata`, `temperatura_metar`, `punto_rocio_metar`, `relh`

## API

### Configuración

Variables de entorno:

| Variable | Valores | Por defecto | Descripción |
| --- | --- | --- | --- |
| `AEROPREDICT_MODEL_SOURCE` | `auto` \| `local` \| `mlflow` | `auto` | `local` fuerza artefacto; `mlflow` ignora artefacto local; `auto` prioriza artefacto local. |
| `AEROPREDICT_ARTIFACT_PATH` | ruta | `models/aeropredict_modelos_finales_v1.pkl` | Ruta al `.pkl`. |
| `AEROPREDICT_RULES_PATH` | ruta | `models/preprocessor_rules.json` | Ruta al `.json`. |

### Endpoints

- `GET /health` → `status`, `model_version` (considera modelo legacy o de dos etapas).
- `POST /predict/delay` → acepta payload legacy + campos opcionales del notebook. Con artefacto local devuelve contrato ampliado:
  - `predicted_delay_minutes` / `minutos_retraso_estimados` (coinciden)
  - `prob_retraso` (0-1), `prediccion_retraso_15min` (bool), `umbral_aplicado` (0.08)
  - `factores` (lista con `id`, `title`, `value`, `contribution`, `direction` en `["increase","decrease"]`, `impact`; `direction: "increase"` = aumenta el riesgo de retraso)
  - `imputaciones` (campos imputados), `model_source` (`local_artifact`), `model_version` (`two-stage-1.0.0`)
- `POST /predict/eta` → devuelve `prob_retraso`, `umbral_aplicado`, `model_source` cuando usa el modelo de dos etapas.

### Compatibilidad

- Payloads existentes siguen válidos (`extra="forbid"` mantenido). Los campos nuevos son opcionales.
- Si no hay artefacto, se usa el modelo MLflow legacy (comportamiento anterior).
- El archivado a `gold.predictions` mantiene compatibilidad.

## Frontend

- `web/src/lib/types.ts`: añade campos opcionales al contrato y el tipo `DelayFactor` con `direction`/`contribution`/`value`.
- `web/src/lib/service.ts`: usa `factores` de la API con su semántica (`direction`) y fallback heurístico; `mock.ts` proporciona factores demo con el nuevo formato.
- `web/src/components/FlightForm.tsx`: añade «Punto de rocío (°C)» y «Humedad relativa (%)» en campos avanzados; envía `origin_iata`, `destination_iata`, `dew_point_c` y `relative_humidity`.
- `web/src/components/ResultPanel.tsx`: muestra «Probabilidad estimada de retraso ≥ 15 min» con el umbral aplicado (se elimina la etiqueta «Confianza del modelo»).
- `web/src/components/FactorList.tsx`: «Factores que influyen en la predicción», agrupados por «Aumentan el riesgo» / «Reducen el riesgo» (top 3 por dirección), muestra valor observado y contribución en log-odds, aclara etapa 1 vs 2.

## Tests

```bash
pytest tests/test_two_stage_predict.py -v
pytest tests/test_api_integration.py -v
```

## Notas

- La métrica de regresión en test es negativa en R² (comportamiento del notebook) — se mantiene tal cual.
- `route_distance` no se usa en las variables del notebook; se mantiene por compatibilidad con el contrato legacy.
