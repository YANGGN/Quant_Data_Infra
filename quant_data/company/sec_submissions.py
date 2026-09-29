"""Publish SEC submissions independently, without creating facts evidence."""
from dataclasses import dataclass
import hashlib
import time
from quant_data.errors import ValidationError, ResourceLimitError
from quant_data.json_codec import loads_strict, dumps_strict
from quant_data.ingestion import IngestionCoordinator, ArtifactWrite, SnapshotWrite, WriteResult, PublicationDeferred
from quant_data.stores import stable_id
from . import sec_companyfacts as sec

NORMALIZATION = "sec_submissions.v1"
FINANCIAL_FORMS = frozenset(("10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A",
    "40-F", "40-F/A", "8-K", "8-K/A", "6-K", "6-K/A"))

@dataclass(frozen=True)
class ParsedSecSubmissions:
    cik: str
    issuer_name: str
    entity_type: str | None
    filings: tuple
    captured_at: str
    submissions_sha256: str
    submissions_byte_count: int
    semantic_identity: str
    scope: dict
    collector_id: str = sec.SEC_COMPANYFACTS_COLLECTOR_ID
    normalization_version: str = NORMALIZATION
    legacy_aapl: bool = False
    facts: tuple = ()

def parse_submissions(*, cik, body, captured_at):
    cik = sec._cik(cik, "selected CIK")
    if not isinstance(body, bytes) or not body:
        raise ValidationError("SEC submissions body is invalid")
    if len(body) > 64 * 1024 * 1024:
        raise ResourceLimitError("SEC submissions body exceeds its bound")
    value = loads_strict(body, max_bytes=64 * 1024 * 1024)
    name, entity_type, filings = sec._submissions_filings(
        sec._mapping(value, "submissions"), cik=cik, maximum_filings=10000)
    scope = {"cik": cik, "filing_scope": "submissions_recent_only",
        "completeness": "complete_for_submissions_recent", "facts_fetched": False}
    semantic = hashlib.sha256(dumps_strict({"normalization": NORMALIZATION,
        "cik": cik, "issuer_name": name, "entity_type": entity_type, "scope": scope,
        "filings": [f.semantic_mapping() for f in sorted(filings, key=lambda f: f.accession_number)]
        }).encode()).hexdigest()
    return ParsedSecSubmissions(cik, name, entity_type, filings, sec._capture_time(captured_at),
        hashlib.sha256(body).hexdigest(), len(body), semantic, scope)

class SecSubmissionsPublisher:
    def __init__(self, stores, registry):
        required = {sec.SEC_AAPL_COMPANYFACTS_EVIDENCE_DATASET_ID,
            sec.SEC_AAPL_COMPANYFACTS_IDENTITY_DATASET_ID, sec.SEC_AAPL_COMPANYFACTS_FILINGS_DATASET_ID,
            sec.SEC_AAPL_COMPANYFACTS_FILING_MEMBERSHIP_DATASET_ID}
        if not required <= {d.id for d in registry.datasets_for("company")}:
            raise ValidationError("SEC submissions datasets are not registered")
        self.coordinator = IngestionCoordinator(stores, code_version=NORMALIZATION)

    def publish(self, parsed, *, deadline=None, monotonic=time.monotonic):
        if not isinstance(parsed, ParsedSecSubmissions):
            raise ValidationError("A parsed SEC submissions response is required")
        def checkpoint():
            if deadline is not None and monotonic() >= deadline:
                raise PublicationDeferred("SEC submissions publication reached its deadline")
        checkpoint()
        run_id = stable_id("sec_submissions_run", parsed.semantic_identity)
        dataset = sec.SEC_AAPL_COMPANYFACTS_FILINGS_DATASET_ID
        evidence = sec.SEC_AAPL_COMPANYFACTS_EVIDENCE_DATASET_ID
        def writer(connection, active_run_id):
            checkpoint()
            count = sec._ensure_issuer(connection, parsed=parsed, run_id=active_run_id)
            artifact, appended = sec._insert_domain_artifact(connection, parsed=parsed,
                endpoint="submissions", content_sha256=parsed.submissions_sha256,
                byte_count=parsed.submissions_byte_count, run_id=active_run_id)
            count += appended
            snapshot, appended = sec._insert_domain_snapshot(connection, parsed=parsed,
                endpoint="submissions", artifact_id=artifact, run_id=active_run_id)
            count += appended
            count += sec._ensure_issuer_version(connection, parsed=parsed,
                submissions_snapshot_id=snapshot, run_id=active_run_id)
            for filing in sorted(parsed.filings, key=lambda f: f.source_row):
                checkpoint()
                count += sec._insert_or_validate_filing(connection, parsed=parsed, filing=filing,
                    submissions_snapshot_id=snapshot, run_id=active_run_id)
                count += sec._insert_filing_membership(connection, submissions_snapshot_id=snapshot,
                    filing=filing, run_id=active_run_id)
            control_artifact = stable_id("sec_submissions_control_artifact", parsed.semantic_identity,
                parsed.submissions_sha256)
            result = WriteResult(written_count=count, quality_results=(), artifacts=(ArtifactWrite(
                artifact_id=control_artifact, dataset_id=evidence,
                content_sha256=parsed.submissions_sha256, media_type="application/json",
                byte_count=parsed.submissions_byte_count,
                source_reference=sec._source_reference(parsed, endpoint="submissions"),
                request_scope=sec._domain_scope(parsed, endpoint="submissions"),
                captured_at=parsed.captured_at, captured_precision="datetime",
                normalization_version=NORMALIZATION),),
                snapshot=SnapshotWrite(snapshot_id=stable_id("sec_submissions_control_snapshot", parsed.semantic_identity),
                    dataset_id=dataset, semantic_identity=parsed.semantic_identity, scope=parsed.scope,
                    completeness="complete", row_count=len(parsed.filings), captured_at=parsed.captured_at,
                    captured_precision="datetime", validation_state="validated", artifact_ids=(control_artifact,)))
            checkpoint()
            return result
        return self.coordinator.execute(role="company", dataset_id=dataset,
            output_dataset_ids=(evidence, sec.SEC_AAPL_COMPANYFACTS_IDENTITY_DATASET_ID, dataset,
                sec.SEC_AAPL_COMPANYFACTS_FILING_MEMBERSHIP_DATASET_ID),
            semantic_identity=parsed.semantic_identity, run_id=run_id, command=parsed.collector_id,
            scope={"request_scope": parsed.scope}, started_at=parsed.captured_at, completed_at=parsed.captured_at,
            fetched_count=len(parsed.filings), writer=writer)
