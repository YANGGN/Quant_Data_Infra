-- Cover run metadata for retained-data capture attribution without table loads.
CREATE INDEX ingestion_runs_status_capture
ON ingestion_runs(run_id, status, snapshot_id, dataset_id);
