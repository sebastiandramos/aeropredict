-- Create gold.predictions table for prediction archival
CREATE SCHEMA IF NOT EXISTS gold;

CREATE TABLE IF NOT EXISTS gold.predictions (
    request_id TEXT PRIMARY KEY,
    model_version TEXT,
    flight_features JSONB,
    predicted_delay_minutes DOUBLE PRECISION,
    predicted_eta TEXT,
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_predictions_timestamp ON gold.predictions (timestamp);
