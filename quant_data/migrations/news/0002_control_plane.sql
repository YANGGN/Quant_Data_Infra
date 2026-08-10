CREATE TABLE dataset_registry_stage2 (
    dataset_id TEXT PRIMARY KEY,
    store_role TEXT NOT NULL CHECK (
        store_role IN ('market', 'macro', 'company', 'news')
    ),
    layer TEXT NOT NULL CHECK (layer IN ('evidence', 'canonical', 'derived')),
    schema_version TEXT NOT NULL,
    relations_json TEXT NOT NULL,
    active INTEGER NOT NULL CHECK (active IN (0, 1)),
    registered_at TEXT NOT NULL,
    last_successful_run_id TEXT,
    last_semantic_identity TEXT
) STRICT;

INSERT INTO dataset_registry_stage2 (
    dataset_id, store_role, layer, schema_version, relations_json, active,
    registered_at, last_successful_run_id, last_semantic_identity
)
SELECT dataset_id, store_role, layer, schema_version, relations_json, active,
       registered_at, last_successful_run_id, last_semantic_identity
FROM dataset_registry;

DROP TABLE dataset_registry;
ALTER TABLE dataset_registry_stage2 RENAME TO dataset_registry;

UPDATE store_metadata SET contract_version = 'stage2' WHERE singleton = 1;

CREATE TRIGGER store_metadata_immutable_insert
BEFORE INSERT ON store_metadata
WHEN EXISTS (
    SELECT 1 FROM store_metadata
    WHERE singleton = NEW.singleton OR store_role = NEW.store_role
)
BEGIN
    SELECT RAISE(ABORT, 'store metadata is immutable');
END;

CREATE TRIGGER store_metadata_immutable_update
BEFORE UPDATE ON store_metadata
BEGIN
    SELECT RAISE(ABORT, 'store metadata is immutable');
END;

CREATE TRIGGER store_metadata_immutable_delete
BEFORE DELETE ON store_metadata
BEGIN
    SELECT RAISE(ABORT, 'store metadata is immutable');
END;

CREATE TABLE dataset_identity_contracts (
    dataset_id TEXT PRIMARY KEY REFERENCES dataset_registry(dataset_id),
    identity_sha256 TEXT NOT NULL CHECK (
        length(identity_sha256) = 64
        AND identity_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    registered_version TEXT NOT NULL
) STRICT;

CREATE TRIGGER dataset_identity_contracts_immutable_update
BEFORE UPDATE ON dataset_identity_contracts
BEGIN
    SELECT RAISE(ABORT, 'dataset identity is immutable');
END;

CREATE TRIGGER dataset_identity_contracts_immutable_insert
BEFORE INSERT ON dataset_identity_contracts
WHEN EXISTS (
    SELECT 1 FROM dataset_identity_contracts
    WHERE dataset_id = NEW.dataset_id
)
BEGIN
    SELECT RAISE(ABORT, 'dataset identity is immutable');
END;

CREATE TRIGGER dataset_identity_contracts_immutable_delete
BEFORE DELETE ON dataset_identity_contracts
BEGIN
    SELECT RAISE(ABORT, 'dataset declarations must be retired, not deleted');
END;

CREATE TABLE ingestion_artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    dataset_id TEXT NOT NULL REFERENCES dataset_registry(dataset_id),
    content_sha256 TEXT NOT NULL CHECK (
        length(content_sha256) = 64
        AND content_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    media_type TEXT NOT NULL,
    byte_count INTEGER NOT NULL CHECK (byte_count > 0),
    source_reference TEXT NOT NULL,
    request_scope_json TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (
        captured_precision IN ('date', 'datetime')
    ),
    normalization_version TEXT NOT NULL
) STRICT;

CREATE INDEX ingestion_artifacts_run
ON ingestion_artifacts(run_id, artifact_id);

CREATE TABLE ingestion_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE REFERENCES ingestion_runs(run_id),
    dataset_id TEXT NOT NULL REFERENCES dataset_registry(dataset_id),
    semantic_identity TEXT NOT NULL CHECK (
        length(semantic_identity) = 64
        AND semantic_identity NOT GLOB '*[^0-9a-f]*'
    ),
    scope_json TEXT NOT NULL,
    completeness TEXT NOT NULL CHECK (completeness IN ('complete', 'partial')),
    row_count INTEGER NOT NULL CHECK (row_count >= 0),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (
        captured_precision IN ('date', 'datetime')
    ),
    validation_state TEXT NOT NULL CHECK (
        validation_state IN ('validated', 'rejected')
    ),
    warnings_json TEXT NOT NULL,
    UNIQUE (dataset_id, semantic_identity)
) STRICT;

CREATE TABLE ingestion_snapshot_artifacts (
    snapshot_id TEXT NOT NULL REFERENCES ingestion_snapshots(snapshot_id),
    artifact_id TEXT NOT NULL REFERENCES ingestion_artifacts(artifact_id),
    artifact_ordinal INTEGER NOT NULL CHECK (artifact_ordinal > 0),
    PRIMARY KEY (snapshot_id, artifact_id),
    UNIQUE (snapshot_id, artifact_ordinal)
) STRICT;

CREATE TABLE data_quality_results (
    quality_result_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    dataset_id TEXT NOT NULL REFERENCES dataset_registry(dataset_id),
    rule_id TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('informational', 'warning', 'critical')),
    outcome TEXT NOT NULL CHECK (outcome IN ('passed', 'failed', 'skipped')),
    subject_kind TEXT NOT NULL CHECK (
        subject_kind IN ('run', 'artifact', 'snapshot', 'version')
    ),
    subject_id TEXT NOT NULL,
    artifact_id TEXT REFERENCES ingestion_artifacts(artifact_id),
    snapshot_id TEXT REFERENCES ingestion_snapshots(snapshot_id),
    observed_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, dataset_id, rule_id, rule_version, subject_kind, subject_id)
) STRICT;

CREATE TABLE ingestion_run_failures (
    failure_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    dataset_id TEXT NOT NULL REFERENCES dataset_registry(dataset_id),
    semantic_identity TEXT NOT NULL CHECK (
        length(semantic_identity) = 64
        AND semantic_identity NOT GLOB '*[^0-9a-f]*'
    ),
    command TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('failed', 'rejected', 'partial')),
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    fetched_count INTEGER NOT NULL CHECK (fetched_count >= 0),
    error_code TEXT NOT NULL,
    code_version TEXT NOT NULL
) STRICT;

CREATE TABLE ingestion_run_outputs (
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    dataset_id TEXT NOT NULL REFERENCES dataset_registry(dataset_id),
    semantic_identity TEXT NOT NULL CHECK (
        length(semantic_identity) = 64
        AND semantic_identity NOT GLOB '*[^0-9a-f]*'
    ),
    PRIMARY KEY (run_id, dataset_id)
) STRICT;

CREATE TRIGGER schema_migrations_immutable_update
BEFORE UPDATE ON schema_migrations
BEGIN
    SELECT RAISE(ABORT, 'applied migration identities are immutable');
END;

CREATE TRIGGER schema_migrations_immutable_insert
BEFORE INSERT ON schema_migrations
WHEN EXISTS (
    SELECT 1 FROM schema_migrations
    WHERE migration_id = NEW.migration_id
       OR ordinal = NEW.ordinal
       OR resource = NEW.resource
)
BEGIN
    SELECT RAISE(ABORT, 'applied migration identities are immutable');
END;

CREATE TRIGGER schema_migrations_immutable_delete
BEFORE DELETE ON schema_migrations
BEGIN
    SELECT RAISE(ABORT, 'applied migration identities are immutable');
END;

CREATE TRIGGER dataset_registry_contract_immutable
BEFORE UPDATE OF dataset_id, store_role, layer, schema_version, relations_json
ON dataset_registry
BEGIN
    SELECT RAISE(ABORT, 'dataset registration identity is immutable');
END;

CREATE TRIGGER dataset_registry_insert_guard
BEFORE INSERT ON dataset_registry
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM dataset_registry WHERE dataset_id = NEW.dataset_id
    ) THEN RAISE(ABORT, 'dataset registration identity is immutable') END;
    SELECT CASE WHEN NEW.store_role != (
        SELECT store_role FROM store_metadata WHERE singleton = 1
    ) THEN RAISE(ABORT, 'dataset registration store role mismatch') END;
END;

CREATE TRIGGER dataset_registry_immutable_delete
BEFORE DELETE ON dataset_registry
BEGIN
    SELECT RAISE(ABORT, 'dataset declarations must be retired, not deleted');
END;

CREATE TRIGGER dataset_registry_success_checkpoint
BEFORE UPDATE OF last_successful_run_id, last_semantic_identity ON dataset_registry
BEGIN
    SELECT CASE WHEN
        (NEW.last_successful_run_id IS NULL) != (NEW.last_semantic_identity IS NULL)
        OR (
            NEW.last_successful_run_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1
                FROM ingestion_runs AS run
                JOIN ingestion_run_outputs AS output
                  ON output.run_id = run.run_id
                 AND output.dataset_id = NEW.dataset_id
                 AND output.semantic_identity = run.semantic_identity
                WHERE run.run_id = NEW.last_successful_run_id
                  AND run.semantic_identity = NEW.last_semantic_identity
                  AND run.status = 'succeeded'
            )
        )
    THEN RAISE(ABORT, 'dataset checkpoint must reference a successful run') END;
END;

CREATE TRIGGER ingestion_runs_digest_insert
BEFORE INSERT ON ingestion_runs
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM ingestion_runs
        WHERE run_id = NEW.run_id
           OR (dataset_id = NEW.dataset_id
               AND semantic_identity = NEW.semantic_identity)
    ) OR EXISTS (
        SELECT 1 FROM ingestion_run_failures WHERE run_id = NEW.run_id
    ) THEN RAISE(ABORT, 'ingestion run identity is immutable') END;
    SELECT CASE WHEN
        length(NEW.semantic_identity) != 64
        OR NEW.semantic_identity GLOB '*[^0-9a-f]*'
    THEN RAISE(ABORT, 'semantic identity must be lowercase SHA-256') END;
    SELECT CASE WHEN NEW.status = 'succeeded'
    THEN RAISE(ABORT, 'successful runs must transition from running') END;
END;

CREATE TRIGGER ingestion_runs_identity_immutable_update
BEFORE UPDATE OF run_id, dataset_id, semantic_identity, command, scope_json,
                 started_at, fetched_count, code_version
ON ingestion_runs
BEGIN
    SELECT RAISE(ABORT, 'ingestion run identity is immutable');
END;

CREATE TRIGGER ingestion_runs_success_lineage
BEFORE UPDATE OF status, artifact_id, snapshot_id ON ingestion_runs
WHEN NEW.status = 'succeeded'
BEGIN
    SELECT CASE WHEN NEW.completed_at IS NULL
        OR NEW.artifact_id IS NULL
        OR NEW.snapshot_id IS NULL
        OR NOT EXISTS (
            SELECT 1 FROM ingestion_artifacts AS artifact
            WHERE artifact.artifact_id = NEW.artifact_id
              AND artifact.run_id = NEW.run_id
        )
        OR NOT EXISTS (
            SELECT 1 FROM ingestion_snapshots AS snapshot
            WHERE snapshot.snapshot_id = NEW.snapshot_id
              AND snapshot.run_id = NEW.run_id
              AND snapshot.validation_state = 'validated'
        )
        OR NOT EXISTS (
            SELECT 1 FROM ingestion_snapshot_artifacts AS membership
            WHERE membership.snapshot_id = NEW.snapshot_id
              AND membership.artifact_id = NEW.artifact_id
        )
    THEN RAISE(ABORT, 'successful run lineage is incomplete') END;
END;

CREATE TRIGGER ingestion_runs_succeeded_immutable
BEFORE UPDATE ON ingestion_runs
WHEN OLD.status = 'succeeded'
BEGIN
    SELECT RAISE(ABORT, 'successful ingestion runs are immutable');
END;

CREATE TRIGGER ingestion_runs_immutable_delete
BEFORE DELETE ON ingestion_runs
BEGIN
    SELECT RAISE(ABORT, 'ingestion runs are immutable');
END;

CREATE TRIGGER ingestion_run_outputs_same_run
BEFORE INSERT ON ingestion_run_outputs
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM ingestion_runs AS run
        WHERE run.run_id = NEW.run_id
          AND run.semantic_identity = NEW.semantic_identity
          AND run.status = 'running'
    ) THEN RAISE(ABORT, 'ingestion output run mismatch') END;
END;

CREATE TRIGGER ingestion_run_outputs_immutable_insert
BEFORE INSERT ON ingestion_run_outputs
WHEN EXISTS (
    SELECT 1 FROM ingestion_run_outputs
    WHERE run_id = NEW.run_id AND dataset_id = NEW.dataset_id
)
BEGIN
    SELECT RAISE(ABORT, 'ingestion run outputs are immutable');
END;

CREATE TRIGGER ingestion_run_outputs_immutable_update
BEFORE UPDATE ON ingestion_run_outputs
BEGIN
    SELECT RAISE(ABORT, 'ingestion run outputs are immutable');
END;

CREATE TRIGGER ingestion_run_outputs_immutable_delete
BEFORE DELETE ON ingestion_run_outputs
BEGIN
    SELECT RAISE(ABORT, 'ingestion run outputs are immutable');
END;

CREATE TRIGGER ingestion_snapshot_artifacts_same_run
BEFORE INSERT ON ingestion_snapshot_artifacts
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM ingestion_snapshots AS snapshot
        JOIN ingestion_artifacts AS artifact
          ON artifact.run_id = snapshot.run_id
        WHERE snapshot.snapshot_id = NEW.snapshot_id
          AND artifact.artifact_id = NEW.artifact_id
    ) THEN RAISE(ABORT, 'snapshot artifact run mismatch') END;
END;

CREATE TRIGGER ingestion_snapshot_artifacts_immutable_insert
BEFORE INSERT ON ingestion_snapshot_artifacts
WHEN EXISTS (
    SELECT 1 FROM ingestion_snapshot_artifacts
    WHERE (snapshot_id = NEW.snapshot_id AND artifact_id = NEW.artifact_id)
       OR (snapshot_id = NEW.snapshot_id
           AND artifact_ordinal = NEW.artifact_ordinal)
)
BEGIN
    SELECT RAISE(ABORT, 'snapshot artifact membership is immutable');
END;

CREATE TRIGGER data_quality_results_same_run
BEFORE INSERT ON data_quality_results
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM data_quality_results
        WHERE quality_result_id = NEW.quality_result_id
           OR (
               run_id = NEW.run_id
               AND dataset_id = NEW.dataset_id
               AND rule_id = NEW.rule_id
               AND rule_version = NEW.rule_version
               AND subject_kind = NEW.subject_kind
               AND subject_id = NEW.subject_id
           )
    ) THEN RAISE(ABORT, 'quality results are immutable') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM ingestion_run_outputs AS output
        WHERE output.run_id = NEW.run_id
          AND output.dataset_id = NEW.dataset_id
    ) THEN RAISE(ABORT, 'quality dataset is not a declared run output') END;
    SELECT CASE WHEN NEW.artifact_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM ingestion_artifacts AS artifact
        WHERE artifact.artifact_id = NEW.artifact_id
          AND artifact.run_id = NEW.run_id
    ) THEN RAISE(ABORT, 'quality artifact run mismatch') END;
    SELECT CASE WHEN NEW.snapshot_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM ingestion_snapshots AS snapshot
        WHERE snapshot.snapshot_id = NEW.snapshot_id
          AND snapshot.run_id = NEW.run_id
    ) THEN RAISE(ABORT, 'quality snapshot run mismatch') END;
    SELECT CASE WHEN NEW.subject_kind = 'run' AND NEW.subject_id != NEW.run_id
    THEN RAISE(ABORT, 'quality run subject mismatch') END;
    SELECT CASE WHEN NEW.subject_kind = 'artifact' AND (
        NEW.artifact_id IS NULL OR NEW.subject_id != NEW.artifact_id
    ) THEN RAISE(ABORT, 'quality artifact subject mismatch') END;
    SELECT CASE WHEN NEW.subject_kind = 'snapshot' AND (
        NEW.snapshot_id IS NULL OR NEW.subject_id != NEW.snapshot_id
    ) THEN RAISE(ABORT, 'quality snapshot subject mismatch') END;
END;

CREATE TRIGGER ingestion_artifacts_insert_guard
BEFORE INSERT ON ingestion_artifacts
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM ingestion_artifacts
        WHERE artifact_id = NEW.artifact_id
    ) THEN RAISE(ABORT, 'ingestion artifacts are immutable') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM ingestion_run_outputs AS output
        WHERE output.run_id = NEW.run_id
          AND output.dataset_id = NEW.dataset_id
    ) THEN RAISE(ABORT, 'artifact dataset is not a declared run output') END;
END;

CREATE TRIGGER ingestion_snapshots_insert_guard
BEFORE INSERT ON ingestion_snapshots
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM ingestion_snapshots
        WHERE snapshot_id = NEW.snapshot_id
           OR run_id = NEW.run_id
           OR (dataset_id = NEW.dataset_id
               AND semantic_identity = NEW.semantic_identity)
    ) THEN RAISE(ABORT, 'ingestion snapshots are immutable') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM ingestion_run_outputs AS output
        WHERE output.run_id = NEW.run_id
          AND output.dataset_id = NEW.dataset_id
          AND output.semantic_identity = NEW.semantic_identity
    ) THEN RAISE(ABORT, 'snapshot dataset is not a declared run output') END;
END;

CREATE TRIGGER ingestion_artifacts_immutable_update
BEFORE UPDATE ON ingestion_artifacts
BEGIN
    SELECT RAISE(ABORT, 'ingestion artifacts are immutable');
END;

CREATE TRIGGER ingestion_artifacts_immutable_delete
BEFORE DELETE ON ingestion_artifacts
BEGIN
    SELECT RAISE(ABORT, 'ingestion artifacts are immutable');
END;

CREATE TRIGGER ingestion_snapshots_immutable_update
BEFORE UPDATE ON ingestion_snapshots
BEGIN
    SELECT RAISE(ABORT, 'ingestion snapshots are immutable');
END;

CREATE TRIGGER ingestion_snapshots_immutable_delete
BEFORE DELETE ON ingestion_snapshots
BEGIN
    SELECT RAISE(ABORT, 'ingestion snapshots are immutable');
END;

CREATE TRIGGER ingestion_snapshot_artifacts_immutable_update
BEFORE UPDATE ON ingestion_snapshot_artifacts
BEGIN
    SELECT RAISE(ABORT, 'snapshot artifact membership is immutable');
END;

CREATE TRIGGER ingestion_snapshot_artifacts_immutable_delete
BEFORE DELETE ON ingestion_snapshot_artifacts
BEGIN
    SELECT RAISE(ABORT, 'snapshot artifact membership is immutable');
END;

CREATE TRIGGER data_quality_results_immutable_update
BEFORE UPDATE ON data_quality_results
BEGIN
    SELECT RAISE(ABORT, 'quality results are immutable');
END;

CREATE TRIGGER data_quality_results_immutable_delete
BEFORE DELETE ON data_quality_results
BEGIN
    SELECT RAISE(ABORT, 'quality results are immutable');
END;

CREATE TRIGGER ingestion_run_failures_immutable_update
BEFORE UPDATE ON ingestion_run_failures
BEGIN
    SELECT RAISE(ABORT, 'failed ingestion runs are immutable');
END;

CREATE TRIGGER ingestion_run_failures_immutable_insert
BEFORE INSERT ON ingestion_run_failures
WHEN EXISTS (
    SELECT 1 FROM ingestion_run_failures
    WHERE failure_id = NEW.failure_id OR run_id = NEW.run_id
) OR EXISTS (
    SELECT 1 FROM ingestion_runs WHERE run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'failed ingestion run identity is immutable');
END;

CREATE TRIGGER ingestion_run_failures_immutable_delete
BEFORE DELETE ON ingestion_run_failures
BEGIN
    SELECT RAISE(ABORT, 'failed ingestion runs are immutable');
END;
