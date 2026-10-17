-- Latest successful retained-data capture, preserving timestamp and binary ID order.
CREATE INDEX ingestion_snapshots_dataset_capture
ON ingestion_snapshots(dataset_id, captured_at DESC, snapshot_id DESC);
