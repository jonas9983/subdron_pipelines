-- Mirrors the columns subdron_utils.DB_Loader.log_mission_stage expects.
-- PIPELINE_ENV != production writes to missions_test.

CREATE TABLE IF NOT EXISTS missions_test (
    id          SERIAL PRIMARY KEY,
    mission_id  TEXT NOT NULL,
    timestamp   TIMESTAMP NOT NULL,
    input_path  TEXT,
    output_path TEXT,
    stage       TEXT,
    status      TEXT,
    ip_method   TEXT,
    cm_method   TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at  TIMESTAMP
);

CREATE TABLE IF NOT EXISTS missions (
    LIKE missions_test INCLUDING ALL
);
