"""Raw Equibles call evidence; source dates never establish historical availability."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re

from ..contracts import IngestionReceipt
from ..errors import ValidationError, ResourceLimitError
from ..ingestion import ArtifactWrite, IngestionCoordinator, SnapshotWrite, WriteResult
from ..json_codec import dumps_strict, loads_strict
from ..stores import StoreRole, acquire_write_session, quiet_immutable_read_connection, stable_id

DATASET = "company.equibles.transcripts"
VERSION = "equibles_transcripts.v1"
MAX_BYTES = 8 * 1024 * 1024
MAX_PAGES = 50
MAX_EVENTS = 1000
SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,19}")


def utc(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("Equibles capture time is invalid") from exc


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValidationError("Equibles integer field is invalid")
    return value


def document(body):
    if not isinstance(body, bytes) or not body or len(body) > MAX_BYTES:
        raise ResourceLimitError("Equibles response exceeds its byte bound")
    value = loads_strict(body.decode("utf-8", errors="strict"))
    if not isinstance(value, dict):
        raise ValidationError("Equibles response must be an object")
    return value


def validate_symbol(symbol):
    if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
        raise ValidationError("Equibles ticker is invalid")
    return symbol


def event_page(body, offset):
    value = document(body)
    rows, meta = value.get("data"), value.get("meta")
    if not isinstance(rows, list) or len(rows) > 100 or not isinstance(meta, dict):
        raise ValidationError("Equibles event envelope is invalid")
    if (integer(meta.get("offset"), 0, MAX_EVENTS) != offset
            or integer(meta.get("count"), 0, 100) != len(rows)
            or meta.get("limit") != 100 or type(meta.get("hasMore")) is not bool
            or (meta["hasMore"] and not rows)):
        raise ValidationError("Equibles event pagination is invalid")
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or not row["id"] or row.get("eventType") != "EarningsCall"
                or type(row.get("hasTranscript")) is not bool):
            raise ValidationError("Equibles event identity is invalid")
        if row["hasTranscript"]:
            integer(row.get("fiscalYear"), 1900, 2200)
            integer(row.get("fiscalQuarter"), 1, 4)
    return rows, meta["hasMore"]


def transcript_page(body, *, symbol, event, offset):
    value = document(body)
    rows = value.get("data")
    if (value.get("ticker") != validate_symbol(symbol) or value.get("eventId") != event["id"]
            or value.get("fiscalYear") != event["fiscalYear"]
            or value.get("fiscalQuarter") != event["fiscalQuarter"]
            or integer(value.get("offset"), 0, 10000) != offset
            or not isinstance(rows, list) or not 1 <= len(rows) <= 200
            or integer(value.get("turnCount"), 1, 200) != len(rows)):
        raise ValidationError("Equibles transcript scope or turns are invalid")
    total = integer(value.get("totalTurnCount"), 1, MAX_PAGES * 200)
    if type(value.get("hasMore")) is not bool or offset + len(rows) > total:
        raise ValidationError("Equibles transcript count is invalid")
    if value["hasMore"] != (offset + len(rows) < total):
        raise ValidationError("Equibles transcript pagination is incomplete")
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("text"), str) or not row["text"].strip():
            raise ValidationError("Equibles transcript text is invalid")
    return value


@dataclass(frozen=True)
class RawPage:
    body: bytes
    captured_at: str
    reference: str
    headers_json: str = "{}"


def validate_bundle(symbol, instrument_id, event, pages):
    validate_symbol(symbol)
    if not isinstance(instrument_id, str) or not instrument_id or len(instrument_id) > 256:
        raise ValidationError("Equibles frozen instrument binding is invalid")
    if not isinstance(pages, tuple) or not 1 <= len(pages) <= MAX_PAGES:
        raise ResourceLimitError("Equibles transcript page bound exceeded")
    if not isinstance(event, dict) or not isinstance(event.get("id"), str) or not event["id"]:
        raise ValidationError("Equibles event identity is invalid")
    integer(event.get("fiscalYear"), 1900, 2200)
    integer(event.get("fiscalQuarter"), 1, 4)
    offset, values, digests = 0, [], set()
    for page in pages:
        if not isinstance(page, RawPage) or not re.fullmatch(r"equibles-transcripts/blobs/[a-f0-9]{64}\.json", page.reference):
            raise ValidationError("Equibles page evidence reference is invalid")
        utc(page.captured_at)
        headers = loads_strict(page.headers_json)
        if not isinstance(headers, dict) or set(headers) - {"x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset", "content-type"}:
            raise ValidationError("Equibles retained headers are invalid")
        digest = hashlib.sha256(page.body).hexdigest()
        if digest in digests or digest + ".json" != page.reference.rsplit("/", 1)[-1]:
            raise ValidationError("Equibles repeated or mismatched page evidence")
        digests.add(digest)
        value = transcript_page(page.body, symbol=symbol, event=event, offset=offset)
        if values and any(value.get(k) != values[0].get(k) for k in ("totalTurnCount", "callDate", "eventTitle")):
            raise ValidationError("Equibles transcript changed during pagination")
        if values and not values[-1]["hasMore"]:
            raise ValidationError("Equibles transcript has an extra page")
        if any(prior["data"] == value["data"] for prior in values):
            raise ValidationError("Equibles repeated transcript turn page")
        values.append(value)
        offset += value["turnCount"]
    if values[-1]["hasMore"] or offset != values[0]["totalTurnCount"]:
        raise ValidationError("Equibles transcript bundle is incomplete")
    return values


class EquiblesTranscriptPublisher:
    def __init__(self, store_map, registry):
        if DATASET not in {d.id for d in registry.datasets_for("company")}:
            raise ValidationError("Equibles transcript dataset is not registered")
        self.stores = store_map
        self.coordinator = IngestionCoordinator(store_map, code_version=VERSION)

    def publish(self, *, symbol, instrument_id, event, pages):
        values = validate_bundle(symbol, instrument_id, event, pages)
        scope = {"provider": "equibles", "symbol": symbol, "instrument_id": instrument_id,
                 "event_id": event["id"], "fiscal_year": event["fiscalYear"],
                 "fiscal_quarter": event["fiscalQuarter"]}
        semantic = hashlib.sha256(dumps_strict({"version": VERSION, "scope": scope, "pages": values}).encode()).hexdigest()
        capture = stable_id("equibles_transcript", semantic)
        latest_at = max(utc(p.captured_at) for p in pages)
        warnings = ("source_call_date_unverified", "availability_is_local_capture", "provider_ticker_binding_at_capture")
        with acquire_write_session(self.stores, (StoreRole.COMPANY,)) as locks:
            with quiet_immutable_read_connection(self.stores, StoreRole.COMPANY) as c:
                exists = c.execute("SELECT capture_id FROM company_equibles_transcripts WHERE semantic_identity=?", (semantic,)).fetchone()
            if exists:
                return IngestionReceipt(outcome="unchanged", store="company", dataset_id=DATASET,
                    semantic_identity=semantic, run_id=None, artifact_id=None, snapshot_id=None,
                    written_count=0, warnings=warnings)
            def writer(c, rid):
                c.execute("INSERT INTO company_equibles_transcripts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (capture, semantic, symbol, instrument_id, event["id"], event["fiscalYear"],
                     event["fiscalQuarter"], dumps_strict(event), values[0]["totalTurnCount"],
                     len(pages), latest_at, dumps_strict(list(warnings)), rid))
                artifacts = []
                for number, (page, value) in enumerate(zip(pages, values)):
                    digest = hashlib.sha256(page.body).hexdigest()
                    artifact = stable_id("equibles_page", capture, str(number), digest)
                    c.execute("INSERT INTO company_equibles_transcript_pages VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (capture, number, value["offset"], value["turnCount"], digest,
                         page.body, utc(page.captured_at), page.reference, page.headers_json, artifact, rid))
                    artifacts.append(ArtifactWrite(artifact, DATASET, digest, "application/json",
                        len(page.body), page.reference, {**scope, "offset": value["offset"]},
                        utc(page.captured_at), "datetime", VERSION))
                snap = SnapshotWrite(stable_id("equibles_control", capture), DATASET, semantic, scope,
                    "complete", values[0]["totalTurnCount"], latest_at, "datetime", "validated",
                    tuple(a.artifact_id for a in artifacts), warnings)
                return WriteResult(1 + len(pages), tuple(artifacts), snap, (), warnings=warnings)
            return self.coordinator.execute(role=StoreRole.COMPANY, dataset_id=DATASET, output_dataset_ids=(DATASET,),
                semantic_identity=semantic, run_id=stable_id("equibles_run", capture),
                command="company.equibles_transcripts", scope=scope,
                started_at=min(utc(p.captured_at) for p in pages), completed_at=latest_at,
                fetched_count=sum(v["turnCount"] for v in values), writer=writer, held_locks=locks)
