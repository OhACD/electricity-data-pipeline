ALTER TABLE statnett_ingestion_runs
    DROP CONSTRAINT statnett_ingestion_runs_archive_sha256_normalizer_version_key;

ALTER TABLE statnett_ingestion_runs
    ADD CONSTRAINT statnett_capture_identity_key
    UNIQUE (archive_sha256, fetched_at, normalizer_version);
