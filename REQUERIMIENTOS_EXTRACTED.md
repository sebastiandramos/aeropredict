# REQUERIMIENTOS FUNCIONALES Y NO FUNCIONALES

> Complete extraction from: `REQUERIMIENTOS FUNCIONALES Y NO FUNCIONALES.docx`


---
# Requerimientos funcionales
---

**RF-01. Ingesta automática de datos aeronáuticos**

El sistema deberá obtener periódicamente información de vuelos y aeronaves desde la API de OpenSky Network.

**RF-02. Ingesta automática de datos meteorológicos**

El sistema deberá recopilar información meteorológica asociada a los aeropuertos seleccionados mediante la API de Open-Meteo

**RF-03. Ingesta de metadatos de aeronaves**

El sistema deberá obtener información descriptiva de las aeronaves (fabricante, modelo, operador y tipo) desde Aircraft Database.

**RF-04. Ejecución programada de la ingesta**

El sistema deberá ejecutar automáticamente los procesos de extracción mediante tareas programadas con frecuencia diaria

**RF-05. Almacenamiento de datos en la Bronze Layer**

El sistema deberá almacenar los datos originales recibidos desde las APIs sin aplicar transformaciones ni modificaciones en Cloudflare R2.

**RF-06. Organización de datos por fuente y fecha**

El sistema deberá organizar los datos almacenados según la fuente de origen y la fecha de ingestión para facilitar su trazabilidad y reprocesamiento

**RF-07. Limpieza y validación de datos**

El sistema deberá aplicar procesos de limpieza, validación y normalización sobre los datos almacenados en la capa Bronze antes de cargarlos en la Trusted Zone.

**RF-08. Eliminación de registros duplicados**

El sistema deberá detectar y eliminar registros duplicados de aeronaves y vuelos utilizando identificadores únicos como ICAO24.

**RF-09. Normalización de formatos**

El sistema deberá normalizar fechas, timestamps, códigos de aeropuerto y tipos de datos para garantizar la consistencia de la información.

**RF-10. Almacenamiento de datos procesados**

El sistema deberá almacenar los datos validados y transformados en MongoDB dentro de la Trusted Zone.

**RF-11. Construcción del dataset enriquecido**

*El sistema deberá generar un conjunto de datos denominado flight_enriched_dataset combinando información de vuelos, aeronaves y meteorología.*

**RF-12. Relación entre fuentes de datos**

El sistema deberá relacionar los vuelos con las aeronaves mediante el identificador ICAO24 y con los datos meteorológicos mediante aeropuerto y timestamp.

**RF-13. Almacenamiento analítico**

El sistema deberá almacenar el dataset enriquecido en PostgreSQL para su explotación analítica posterior.

**RF-14. Soporte para análisis predictivo**

El sistema deberá proporcionar datos estructurados que permitan desarrollar futuros modelos predictivos de retrasos y estimación de llegadas.


## Requisitos futuros del sistema

**RF-F01. Predicción de retrasos**

El sistema deberá predecir la probabilidad de que un vuelo sufra retrasos antes de su salida o llegada.

**RF-F02. Estimación de hora de llegada (ETA)**

El sistema deberá calcular una estimación actualizada de la hora de llegada de una aeronave utilizando datos operacionales y meteorológicos.

**RF-F03. Entrenamiento automático de modelos**

El sistema deberá permitir el entrenamiento periódico de modelos de Machine Learning utilizando los datos almacenados en la explotación de datos.

**RF-F04. Reentrenamiento del modelo**

El sistema deberá actualizar los modelos predictivos cuando se incorporen nuevos datos históricos relevantes.

**RF-F05. Consulta de predicciones**

Los usuarios deberán poder consultar la predicción de retraso de un vuelo específico.

**RF-F06. Visualización de resultados**

El sistema deberá mostrar los resultados de las predicciones mediante dashboards y gráficos interactivos.

**RF-F07. Comparación entre predicción y realidad**

El sistema deberá almacenar las predicciones realizadas y compararlas posteriormente con los resultados reales para evaluar el modelo.

**RF-F08. Análisis de congestión aeroportuaria**

El sistema deberá identificar patrones de congestión en aeropuertos a partir del volumen de vuelos registrados.


---
# Requerimientos no funcionales
---

**RNF-01. Escalabilidad**

La arquitectura deberá soportar el crecimiento del volumen de datos procedente de múltiples fuentes aeronáuticas sin afectar significativamente al rendimiento.

**RNF-02. Disponibilidad de los datos**

Los datos almacenados deberán estar accesibles para todos los miembros del equipo desde una ubicación centralizada.

**RNF-03. Trazabilidad**

El sistema deberá mantener información sobre la procedencia y fecha de ingestión de todos los datos almacenados.

**RNF-04. Reproducibilidad**

Las ejecuciones del pipeline deberán poder repetirse obteniendo resultados consistentes a partir de los mismos datos de entrada.

**RNF-05. Seguridad de credenciales**

Las claves de acceso y secretos utilizados por las APIs deberán almacenarse de forma segura mediante Doppler y no podrán estar embebidos en el código fuente.

**RNF-06. Portabilidad**

La solución deberá poder ejecutarse en diferentes entornos mediante contenedores Docker.

**RNF-07. Mantenibilidad**

El código deberá estar versionado mediante GitHub para facilitar el mantenimiento, la colaboración y la evolución del proyecto.

**RNF-08. Integridad de datos**

Los procesos de transformación deberán garantizar la consistencia y validez de los datos almacenados en la Trusted Zone.

**RNF-09. Compatibilidad**

La arquitectura deberá ser compatible con servicios S3 mediante Cloudflare R2 y con bases de datos MongoDB y PostgreSQL

**RNF-10. Eficiencia en el consumo de APIs**

El sistema deberá minimizar el número de llamadas a las APIs externas para respetar los límites de créditos y peticiones disponibles.


## Requerimientos no funcionales futuros

**RNF-F01. Precisión predictiva**

El modelo deberá alcanzar una precisión mínima definida

**RNF-F02. Tiempo de respuesta**

Las predicciones deberán generarse en menos de 5 segundos desde la solicitud del usuario.

**RNF-F03. Escalabilidad analítica**

La plataforma deberá soportar el procesamiento de millones de registros históricos de vuelos.

**RNF-F04. Interpretabilidad**

Las predicciones deberán poder justificarse mediante las variables más influyentes utilizadas por el modelo.

**RNF-F05. Disponibilidad**

La plataforma deberá estar disponible al menos el 99% del tiempo durante su operación.


---
*End of document*