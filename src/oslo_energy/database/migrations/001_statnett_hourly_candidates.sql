CREATE TABLE statnett_ingestion_runs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    requested_from_date DATE,
    fetched_at TIMESTAMPTZ NOT NULL,
    archive_path TEXT NOT NULL,
    archive_sha256 TEXT NOT NULL CHECK (archive_sha256 ~ '^[0-9a-f]{64}$'),
    frequency TEXT NOT NULL CHECK (frequency = 'hourly'),
    period_tick_ms INTEGER NOT NULL CHECK (period_tick_ms = 3600000),
    normalizer_version TEXT NOT NULL,
    quality_status TEXT NOT NULL DEFAULT 'candidate_not_training_ready',
    quality_report JSONB NOT NULL,
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    persisted_count INTEGER NOT NULL CHECK (persisted_count >= 0),
    training_ready BOOLEAN NOT NULL DEFAULT FALSE CHECK (training_ready = FALSE),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (archive_sha256, normalizer_version)
);

CREATE TABLE statnett_hourly_observations_current (
    period_start_utc TIMESTAMPTZ PRIMARY KEY,
    period_end_utc TIMESTAMPTZ NOT NULL,
    observation_date DATE NOT NULL,
    source_index INTEGER NOT NULL CHECK (source_index >= 0),
    production DOUBLE PRECISION,
    consumption DOUBLE PRECISION,
    first_seen_run_id BIGINT NOT NULL REFERENCES statnett_ingestion_runs(id),
    last_seen_run_id BIGINT NOT NULL REFERENCES statnett_ingestion_runs(id),
    first_seen_fetch_at TIMESTAMPTZ NOT NULL,
    last_seen_fetch_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (EXTRACT(MINUTE FROM period_start_utc) = 0 AND EXTRACT(SECOND FROM period_start_utc) = 0),
    CHECK (period_end_utc = period_start_utc + INTERVAL '1 hour'),
    CHECK (observation_date = (period_start_utc AT TIME ZONE 'Europe/Oslo')::DATE),
    CHECK (last_seen_fetch_at >= first_seen_fetch_at),
    CHECK (production IS NULL OR (production >= 0 AND production < 'Infinity'::DOUBLE PRECISION)),
    CHECK (consumption IS NULL OR (consumption >= 0 AND consumption < 'Infinity'::DOUBLE PRECISION))
);

CREATE TABLE statnett_observation_revisions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    period_start_utc TIMESTAMPTZ NOT NULL,
    previous_run_id BIGINT NOT NULL REFERENCES statnett_ingestion_runs(id),
    new_run_id BIGINT NOT NULL REFERENCES statnett_ingestion_runs(id),
    old_production DOUBLE PRECISION,
    new_production DOUBLE PRECISION,
    old_consumption DOUBLE PRECISION,
    new_consumption DOUBLE PRECISION,
    source_fetched_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (old_production IS DISTINCT FROM new_production OR old_consumption IS DISTINCT FROM new_consumption)
);

CREATE INDEX statnett_observations_local_date_idx
    ON statnett_hourly_observations_current (observation_date);
CREATE INDEX statnett_revisions_period_idx
    ON statnett_observation_revisions (period_start_utc, recorded_at);