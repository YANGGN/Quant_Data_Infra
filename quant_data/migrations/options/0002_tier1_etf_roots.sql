-- Run within one transaction with foreign_keys OFF, then validate before commit.
-- Rebuild only the two SPY-restricted parent tables, copying every value unchanged.
CREATE TABLE option_captures_expanded (
 capture_id INTEGER PRIMARY KEY,
 symbol TEXT NOT NULL CHECK(symbol IN ('SPY','QQQ','IWM','DIA','XLB','XLC','XLE','XLF','XLI','XLK','XLP','XLRE','XLU','XLV','XLY')),
 session_date TEXT NOT NULL, predecessor_id INTEGER REFERENCES option_captures,
 semantic_sha256 TEXT NOT NULL CHECK(length(semantic_sha256)=64),
 captured_at TEXT NOT NULL, underlying_price TEXT NOT NULL, metadata BLOB NOT NULL
) STRICT;
INSERT INTO option_captures_expanded SELECT * FROM option_captures;
DROP TABLE option_captures;
ALTER TABLE option_captures_expanded RENAME TO option_captures;
CREATE INDEX option_capture_date ON option_captures(symbol,session_date,capture_id);
CREATE TABLE option_contracts_expanded (
 contract_id TEXT PRIMARY KEY,
 provider_root TEXT NOT NULL CHECK(provider_root IN ('SPY','QQQ','IWM','DIA','XLB','XLC','XLE','XLF','XLI','XLK','XLP','XLRE','XLU','XLV','XLY')),
 expiration TEXT NOT NULL, strike TEXT NOT NULL, right TEXT NOT NULL CHECK(right IN ('C','P')),
 deliverable_state TEXT NOT NULL CHECK(deliverable_state='provider_root_only_unverified')
) STRICT;
INSERT INTO option_contracts_expanded SELECT * FROM option_contracts;
DROP TABLE option_contracts;
ALTER TABLE option_contracts_expanded RENAME TO option_contracts;
CREATE INDEX option_contract_expiry ON option_contracts(expiration,right);
CREATE TRIGGER immutable_option_captures_UPDATE BEFORE UPDATE ON option_captures BEGIN SELECT RAISE(ABORT,'immutable options facts'); END;
CREATE TRIGGER immutable_option_captures_DELETE BEFORE DELETE ON option_captures BEGIN SELECT RAISE(ABORT,'immutable options facts'); END;
CREATE TRIGGER immutable_option_contracts_UPDATE BEFORE UPDATE ON option_contracts BEGIN SELECT RAISE(ABORT,'immutable options facts'); END;
CREATE TRIGGER immutable_option_contracts_DELETE BEFORE DELETE ON option_contracts BEGIN SELECT RAISE(ABORT,'immutable options facts'); END;
