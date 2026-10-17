CREATE TABLE store_metadata (
 store_role TEXT PRIMARY KEY CHECK(store_role='options'), contract_version TEXT NOT NULL
) STRICT;
CREATE TABLE schema_migrations (
 ordinal INTEGER PRIMARY KEY, migration_id TEXT NOT NULL UNIQUE,
 resource TEXT NOT NULL, sha256 TEXT NOT NULL, applied_at TEXT NOT NULL
) STRICT;
CREATE TABLE dataset_registry (
 id TEXT PRIMARY KEY, layer TEXT NOT NULL CHECK(layer IN ('evidence','canonical','research')),
 version TEXT NOT NULL
) STRICT;
CREATE TABLE option_captures (
 capture_id INTEGER PRIMARY KEY, symbol TEXT NOT NULL CHECK(symbol='SPY'),
 session_date TEXT NOT NULL, predecessor_id INTEGER REFERENCES option_captures,
 semantic_sha256 TEXT NOT NULL CHECK(length(semantic_sha256)=64),
 captured_at TEXT NOT NULL, underlying_price TEXT NOT NULL, metadata BLOB NOT NULL
) STRICT;
CREATE INDEX option_capture_date ON option_captures(symbol,session_date,capture_id);
CREATE TABLE option_current (
 symbol TEXT NOT NULL, session_date TEXT NOT NULL,
 capture_id INTEGER NOT NULL REFERENCES option_captures,
 PRIMARY KEY(symbol,session_date)
) STRICT;
CREATE TABLE option_contracts (
 contract_id TEXT PRIMARY KEY, provider_root TEXT NOT NULL CHECK(provider_root='SPY'),
 expiration TEXT NOT NULL, strike TEXT NOT NULL, right TEXT NOT NULL CHECK(right IN ('C','P')),
 deliverable_state TEXT NOT NULL CHECK(deliverable_state='provider_root_only_unverified')
) STRICT;
CREATE INDEX option_contract_expiry ON option_contracts(expiration,right);
CREATE TABLE option_details (
 capture_id INTEGER NOT NULL REFERENCES option_captures,
 contract_id TEXT NOT NULL REFERENCES option_contracts,
 volume INTEGER CHECK(volume>=0), open_interest INTEGER CHECK(open_interest>=0),
 bid TEXT, ask TEXT, implied_vol TEXT, reasons TEXT NOT NULL, payload BLOB NOT NULL,
 PRIMARY KEY(capture_id,contract_id)
) STRICT;
CREATE TABLE option_daily_summaries (
 capture_id INTEGER NOT NULL REFERENCES option_captures,
 population TEXT NOT NULL CHECK(population IN ('full','selected','remainder')),
 dimension TEXT NOT NULL CHECK(dimension IN ('overall','dte','moneyness')),
 bucket TEXT NOT NULL, right TEXT NOT NULL CHECK(right IN ('C','P')),
 payload BLOB NOT NULL,
 PRIMARY KEY(capture_id,population,dimension,bucket,right)
) STRICT;
CREATE TABLE option_activity_leaders (
 capture_id INTEGER NOT NULL REFERENCES option_captures, population TEXT NOT NULL,
 metric TEXT NOT NULL, right TEXT NOT NULL, rank INTEGER NOT NULL CHECK(rank BETWEEN 1 AND 5),
 contract_id TEXT NOT NULL, payload BLOB NOT NULL,
 PRIMARY KEY(capture_id,population,metric,right,rank)
) STRICT;
CREATE TABLE option_source_receipts (
 capture_id INTEGER NOT NULL REFERENCES option_captures, ordinal INTEGER NOT NULL,
 method TEXT NOT NULL, response_sha256 TEXT NOT NULL,
 response_bytes INTEGER NOT NULL, payload BLOB NOT NULL,
 PRIMARY KEY(capture_id,ordinal)
) STRICT;
CREATE TRIGGER immutable_option_captures_UPDATE BEFORE UPDATE ON option_captures BEGIN SELECT RAISE(ABORT,'immutable options facts'); END;
CREATE TRIGGER immutable_option_captures_DELETE BEFORE DELETE ON option_captures BEGIN SELECT RAISE(ABORT,'immutable options facts'); END;
CREATE TRIGGER immutable_option_contracts_UPDATE BEFORE UPDATE ON option_contracts BEGIN SELECT RAISE(ABORT,'immutable options facts'); END;
CREATE TRIGGER immutable_option_contracts_DELETE BEFORE DELETE ON option_contracts BEGIN SELECT RAISE(ABORT,'immutable options facts'); END;
CREATE TRIGGER immutable_option_details_UPDATE BEFORE UPDATE ON option_details BEGIN SELECT RAISE(ABORT,'immutable options facts'); END;
CREATE TRIGGER immutable_option_details_DELETE BEFORE DELETE ON option_details BEGIN SELECT RAISE(ABORT,'immutable options facts'); END;
CREATE TRIGGER immutable_option_daily_summaries_UPDATE BEFORE UPDATE ON option_daily_summaries BEGIN SELECT RAISE(ABORT,'immutable options facts'); END;
CREATE TRIGGER immutable_option_daily_summaries_DELETE BEFORE DELETE ON option_daily_summaries BEGIN SELECT RAISE(ABORT,'immutable options facts'); END;
CREATE TRIGGER immutable_option_activity_leaders_UPDATE BEFORE UPDATE ON option_activity_leaders BEGIN SELECT RAISE(ABORT,'immutable options facts'); END;
CREATE TRIGGER immutable_option_activity_leaders_DELETE BEFORE DELETE ON option_activity_leaders BEGIN SELECT RAISE(ABORT,'immutable options facts'); END;
CREATE TRIGGER immutable_option_source_receipts_UPDATE BEFORE UPDATE ON option_source_receipts BEGIN SELECT RAISE(ABORT,'immutable options facts'); END;
CREATE TRIGGER immutable_option_source_receipts_DELETE BEFORE DELETE ON option_source_receipts BEGIN SELECT RAISE(ABORT,'immutable options facts'); END;
CREATE TRIGGER immutable_schema_migrations_UPDATE BEFORE UPDATE ON schema_migrations BEGIN SELECT RAISE(ABORT,'immutable options facts'); END;
CREATE TRIGGER immutable_schema_migrations_DELETE BEFORE DELETE ON schema_migrations BEGIN SELECT RAISE(ABORT,'immutable options facts'); END;
CREATE TRIGGER immutable_dataset_registry_UPDATE BEFORE UPDATE ON dataset_registry BEGIN SELECT RAISE(ABORT,'immutable options facts'); END;
CREATE TRIGGER immutable_dataset_registry_DELETE BEFORE DELETE ON dataset_registry BEGIN SELECT RAISE(ABORT,'immutable options facts'); END;
CREATE TRIGGER immutable_store_metadata_UPDATE BEFORE UPDATE ON store_metadata BEGIN SELECT RAISE(ABORT,'immutable options facts'); END;
CREATE TRIGGER immutable_store_metadata_DELETE BEFORE DELETE ON store_metadata BEGIN SELECT RAISE(ABORT,'immutable options facts'); END;
