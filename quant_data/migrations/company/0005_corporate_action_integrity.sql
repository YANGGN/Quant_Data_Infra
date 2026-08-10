-- Deliberate Stage 4 reconstruction of corporate-action integrity triggers.

CREATE TRIGGER company_action_source_artifacts_immutable_update
BEFORE UPDATE ON company_action_source_artifacts
BEGIN
    SELECT RAISE(ABORT, 'corporate action artifacts are immutable');
END;

CREATE TRIGGER company_action_source_artifacts_immutable_delete
BEFORE DELETE ON company_action_source_artifacts
BEGIN
    SELECT RAISE(ABORT, 'corporate action artifacts must not be deleted');
END;

CREATE TRIGGER company_action_snapshots_artifact_lineage_guard
BEFORE INSERT ON company_action_snapshots
WHEN NOT EXISTS (
    SELECT 1
    FROM company_action_source_artifacts
    WHERE artifact_id = NEW.artifact_id
      AND issuer_id = NEW.issuer_id
      AND provider = NEW.provider
      AND run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'corporate action snapshot artifact/run mismatch');
END;

CREATE TRIGGER company_action_snapshots_immutable_update
BEFORE UPDATE ON company_action_snapshots
BEGIN
    SELECT RAISE(ABORT, 'corporate action snapshots are immutable');
END;

CREATE TRIGGER company_action_snapshots_immutable_delete
BEFORE DELETE ON company_action_snapshots
BEGIN
    SELECT RAISE(ABORT, 'corporate action snapshots must not be deleted');
END;

CREATE TRIGGER company_corporate_action_versions_snapshot_scope_guard
BEFORE INSERT ON company_corporate_action_versions
WHEN NOT EXISTS (
    SELECT 1
    FROM company_action_snapshots
    WHERE snapshot_id = NEW.source_snapshot_id
      AND issuer_id = NEW.issuer_id
      AND provider = NEW.provider
      AND run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'corporate action must match source snapshot scope');
END;

CREATE TRIGGER company_corporate_action_versions_initial_guard
BEFORE INSERT ON company_corporate_action_versions
WHEN NEW.supersedes_action_version_id IS NULL
 AND (NEW.version_sequence <> 1 OR NEW.action_state <> 'active')
BEGIN
    SELECT RAISE(ABORT, 'initial corporate action must be active sequence one');
END;

CREATE TRIGGER company_corporate_action_versions_supersession_guard
BEFORE INSERT ON company_corporate_action_versions
WHEN NEW.supersedes_action_version_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM company_corporate_action_versions AS previous
        WHERE previous.action_version_id = NEW.supersedes_action_version_id
          AND previous.issuer_id = NEW.issuer_id
          AND previous.provider = NEW.provider
          AND previous.provider_event_id = NEW.provider_event_id
          AND previous.version_sequence + 1 = NEW.version_sequence
          AND (
              (previous.action_state = 'active'
               AND NEW.action_state IN ('active', 'tombstone'))
              OR (
                  previous.action_state = 'tombstone'
                  AND NEW.action_state = 'active'
              )
          )
    ) THEN RAISE(ABORT, 'invalid corporate action supersession') END;
END;

CREATE TRIGGER company_corporate_action_versions_tombstone_scope_guard
BEFORE INSERT ON company_corporate_action_versions
WHEN NEW.action_state = 'tombstone'
 AND NOT EXISTS (
    SELECT 1
    FROM company_action_snapshots
    WHERE snapshot_id = NEW.source_snapshot_id
      AND completeness = 'complete'
      AND tombstone_authoritative = 1
)
BEGIN
    SELECT RAISE(ABORT, 'corporate action tombstone requires complete authoritative snapshot');
END;

CREATE TRIGGER company_corporate_action_versions_immutable_update
BEFORE UPDATE ON company_corporate_action_versions
BEGIN
    SELECT RAISE(ABORT, 'corporate action versions are immutable');
END;

CREATE TRIGGER company_corporate_action_versions_immutable_delete
BEFORE DELETE ON company_corporate_action_versions
BEGIN
    SELECT RAISE(ABORT, 'corporate action versions must not be deleted');
END;

CREATE TRIGGER company_action_snapshot_membership_scope_guard
BEFORE INSERT ON company_action_snapshot_membership
WHEN NOT EXISTS (
    SELECT 1
    FROM company_action_snapshots AS snapshot
    JOIN company_corporate_action_versions AS action
      ON action.action_version_id = NEW.action_version_id
    WHERE snapshot.snapshot_id = NEW.snapshot_id
      AND snapshot.issuer_id = action.issuer_id
      AND snapshot.provider = action.provider
      AND snapshot.run_id = NEW.run_id
      AND action.run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'corporate action membership must match snapshot scope');
END;

CREATE TRIGGER company_action_snapshot_membership_immutable_update
BEFORE UPDATE ON company_action_snapshot_membership
BEGIN
    SELECT RAISE(ABORT, 'corporate action snapshot membership is immutable');
END;

CREATE TRIGGER company_action_snapshot_membership_immutable_delete
BEFORE DELETE ON company_action_snapshot_membership
BEGIN
    SELECT RAISE(ABORT, 'corporate action snapshot membership must not be deleted');
END;


CREATE TRIGGER ingestion_runs_company_action_success_domain_guard
BEFORE UPDATE OF status, artifact_id, snapshot_id ON ingestion_runs
WHEN NEW.status = 'succeeded'
 AND EXISTS (
    SELECT 1 FROM company_action_source_artifacts
    WHERE run_id = NEW.run_id
 )
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1
        FROM company_action_source_artifacts AS artifact
        WHERE artifact.run_id = NEW.run_id
          AND NOT EXISTS (
              SELECT 1
              FROM company_action_snapshots AS snapshot
              WHERE snapshot.artifact_id = artifact.artifact_id
                AND snapshot.run_id = NEW.run_id
          )
    ) THEN RAISE(ABORT, 'successful corporate-action run has an artifact without a domain snapshot') END;
END;


CREATE TRIGGER company_action_source_artifacts_running_run_guard
BEFORE INSERT ON company_action_source_artifacts
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id
      AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'corporate action artifacts require a running ingestion run');
END;

CREATE TRIGGER company_action_snapshots_running_run_guard
BEFORE INSERT ON company_action_snapshots
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id
      AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'corporate action snapshots require a running ingestion run');
END;


CREATE TRIGGER company_corporate_action_versions_running_run_guard
BEFORE INSERT ON company_corporate_action_versions
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'corporate action versions require a running ingestion run');
END;

CREATE TRIGGER company_action_snapshot_membership_running_run_guard
BEFORE INSERT ON company_action_snapshot_membership
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'corporate action membership requires a running ingestion run');
END;

CREATE TRIGGER ingestion_runs_company_action_success_membership_guard
BEFORE UPDATE OF status, artifact_id, snapshot_id ON ingestion_runs
WHEN NEW.status = 'succeeded'
 AND EXISTS (
    SELECT 1 FROM company_corporate_action_versions
    WHERE run_id = NEW.run_id
 )
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1
        FROM company_corporate_action_versions AS action
        WHERE action.run_id = NEW.run_id
          AND NOT EXISTS (
              SELECT 1 FROM company_action_snapshot_membership AS membership
              WHERE membership.action_version_id = action.action_version_id
                AND membership.run_id = NEW.run_id
          )
    ) THEN RAISE(ABORT, 'successful corporate-action run has an action without snapshot membership') END;
END;

