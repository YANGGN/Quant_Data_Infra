-- Seek current capture metadata and shared output anchors without wide history scans.
CREATE INDEX ingestion_snapshots_dataset_capture
ON ingestion_snapshots(dataset_id, captured_at DESC, snapshot_id DESC);

CREATE INDEX ingestion_runs_status_capture
ON ingestion_runs(run_id, status, snapshot_id, dataset_id);

CREATE INDEX ingestion_run_outputs_dataset
ON ingestion_run_outputs(dataset_id, run_id);
