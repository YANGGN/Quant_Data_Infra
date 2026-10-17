-- Filter shared capture membership before reading run or snapshot metadata.
CREATE INDEX ingestion_run_outputs_dataset
ON ingestion_run_outputs(dataset_id, run_id);
