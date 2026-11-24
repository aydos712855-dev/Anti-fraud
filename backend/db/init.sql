-- TimescaleDB extension for time-series data
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- Convert trades table to hypertable for efficient time-series queries
-- (run after SQLAlchemy creates the table)
-- SELECT create_hypertable('trades', 'open_time', if_not_exists => TRUE);
-- SELECT create_hypertable('risk_scores', 'computed_at', if_not_exists => TRUE);
-- SELECT create_hypertable('audit_logs', 'created_at', if_not_exists => TRUE);
