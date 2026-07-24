-- vrr_agent_open — PostgreSQL schema (data + compute + pgvector knowledge index).
-- Mirrors the Databricks three-schema layout; registered in Unity Catalog OSS as
-- the governance catalog-of-record (see governance/uc_register.py).
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS vrr_raw;
CREATE SCHEMA IF NOT EXISTS vrr_curated;
CREATE SCHEMA IF NOT EXISTS vrr_agent;

-- ---- raw (source-shaped) --------------------------------------------------
CREATE TABLE IF NOT EXISTS vrr_raw.production_volumes_daily (
  pattern_id text, completion_id text, vrr_date date, factor double precision,
  oil double precision, water double precision, gas double precision,
  water_inj double precision, gas_inj double precision, amount_type text
);
CREATE TABLE IF NOT EXISTS vrr_raw.pattern_pressure (
  pattern_id text, vrr_date date, pressure_psi double precision
);
CREATE TABLE IF NOT EXISTS vrr_raw.completion_pvt (
  completion_id text, pressure_psi double precision,
  bo double precision, bw double precision, bg double precision,
  rs double precision, rv double precision,
  bw_inj double precision, bg_inj double precision
);
CREATE TABLE IF NOT EXISTS vrr_raw.pattern_target (
  pattern_id text, target_vrr double precision
);

-- ---- curated (lineage layer + VRR aggregates) -----------------------------
-- completion_contrib is the LINEAGE LAYER: one row per (pattern,completion,date)
-- carrying every root input AND every derived reservoir term (from core/physics.py).
CREATE TABLE IF NOT EXISTS vrr_curated.completion_contrib (
  pattern_id text, completion_id text, vrr_date date, factor double precision,
  oil double precision, water double precision, gas double precision,
  water_inj double precision, gas_inj double precision,
  pressure_psi double precision, pvt_method text,
  oil_res double precision, water_res double precision, free_gas_res double precision,
  water_inj_res double precision, gas_inj_res double precision,
  run_id text, built_at timestamptz DEFAULT now()
);
CREATE TABLE IF NOT EXISTS vrr_curated.pattern_vrr_monthly (
  pattern_id text, pattern_name text, vrr_date date,
  prod_res_bbl double precision, inj_res_bbl double precision, vrr double precision,
  n_completions int, any_extrapolated boolean, run_id text, built_at timestamptz DEFAULT now()
);

-- ---- agent (memory + queue + knowledge) -----------------------------------
CREATE TABLE IF NOT EXISTS vrr_agent.pattern_memory (
  pattern_id text PRIMARY KEY, pattern_name text,
  latest_vrr double precision, latest_date date,
  typical_low double precision, typical_high double precision,
  response_factor double precision DEFAULT 1.0, n_adjustments int DEFAULT 0,
  tendencies text, updated_at timestamptz
);
CREATE TABLE IF NOT EXISTS vrr_agent.adjustment_history (
  action_id text, pattern_id text, pattern_name text, vrr_date date, driver text,
  anomaly text, change_type text, d_inj_res_bbl double precision, d_surface_pct double precision,
  pre_vrr double precision, predicted_post_vrr double precision, actual_post_vrr double precision,
  decision text, approved_by text, outcome text, ts timestamptz DEFAULT now()
);
CREATE TABLE IF NOT EXISTS vrr_agent.safety_limits (
  pattern_id text, completion_id text, max_inj_rate_change_pct double precision,
  max_inj_pressure double precision, fracture_gradient double precision, note text
);
CREATE TABLE IF NOT EXISTS vrr_agent.action_queue (
  action_id text PRIMARY KEY, pattern_id text, pattern_name text, vrr_date date,
  anomaly_kind text, severity text, anomaly_detail text, driver text, action_type text,
  recommendation jsonb, precedent jsonb, confidence text, narrative text,
  stage text DEFAULT 'draft', stage_by text, stage_ts timestamptz,
  run_id text, created_at timestamptz DEFAULT now()
);
-- semantic memory: PII-redacted chunks + pgvector embedding (the knowledge index)
CREATE TABLE IF NOT EXISTS vrr_agent.reservoir_knowledge (
  chunk_id text PRIMARY KEY, doc_id text, file_name text, page int, chunk_seq int,
  text text, pii_redacted boolean, embedding vector(768), ingested_at timestamptz DEFAULT now()
);
CREATE TABLE IF NOT EXISTS vrr_agent.knowledge_registry (
  doc_id text PRIMARY KEY, file_name text, status text DEFAULT 'pending_review',
  reviewed_by text, pii_found boolean, pii_kinds text, n_chunks int, registered_at timestamptz DEFAULT now()
);
