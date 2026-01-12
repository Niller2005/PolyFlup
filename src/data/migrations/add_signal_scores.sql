-- Migration: Add raw signal scores for confidence calibration
-- This allows backtesting different confidence formula variants

ALTER TABLE trades ADD COLUMN up_total REAL;
ALTER TABLE trades ADD COLUMN down_total REAL;
ALTER TABLE trades ADD COLUMN momentum_score REAL;
ALTER TABLE trades ADD COLUMN momentum_dir TEXT;
ALTER TABLE trades ADD COLUMN flow_score REAL;
ALTER TABLE trades ADD COLUMN flow_dir TEXT;
ALTER TABLE trades ADD COLUMN divergence_score REAL;
ALTER TABLE trades ADD COLUMN divergence_dir TEXT;
ALTER TABLE trades ADD COLUMN vwm_score REAL;
ALTER TABLE trades ADD COLUMN vwm_dir TEXT;
ALTER TABLE trades ADD COLUMN pm_mom_score REAL;
ALTER TABLE trades ADD COLUMN pm_mom_dir TEXT;
ALTER TABLE trades ADD COLUMN adx_score REAL;
ALTER TABLE trades ADD COLUMN adx_dir TEXT;
ALTER TABLE trades ADD COLUMN lead_lag_bonus REAL;

-- Update schema version
INSERT INTO schema_version (version, applied_at) VALUES (6, datetime('now'));
