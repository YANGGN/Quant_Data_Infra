"""Immutable local collection manifests; provider mapping is a separate step.

Internal services accept explicit StoreMap capabilities. No network or credential
access, implicit provider-symbol normalization, or operational-default fallback.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import PurePosixPath

from ..contracts import IngestionReceipt
from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..ingestion import ArtifactWrite, IngestionCoordinator, SnapshotWrite, WriteResult
from ..json_codec import dumps_strict, loads_strict
from ..stores import StoreMap, StoreRole, acquire_write_session, quiet_immutable_read_connection, stable_id

EVIDENCE = "market.collection.evidence"
MEMBERSHIP = "market.collection.membership"
VERSION = "collection_manifest.v1"
MAX_MEMBERS = 5000
MAX_BYTES = 8 * 1024 * 1024
_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.\^-]{0,31}")
_ID = re.compile(r"[a-z][a-z0-9_]{0,79}")


def _digest(value):
    return hashlib.sha256(dumps_strict(value).encode()).hexdigest()


def _utc(value):
    if not isinstance(value, str):
        raise ValidationError("Collection capture time must be an aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
    except ValueError as exc:
        raise ValidationError("Collection capture time must be an aware timestamp") from exc
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _text(value, label, maximum=256):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise ValidationError("Invalid collection " + label)
    return value


@dataclass(frozen=True)
class ManifestMember:
    symbol: str
    name: str
    source_row: int
    payload_json: str


@dataclass(frozen=True)
class CollectionManifest:
    universe_id: str
    name: str
    source_namespace: str
    source_reference: str
    source_label_date: str | None
    captured_at: str
    body: bytes
    members: tuple[ManifestMember, ...]
    state_sha256: str
    membership_sha256: str

    @property
    def content_sha256(self):
        return hashlib.sha256(self.body).hexdigest()

    @property
    def acquisition_identity(self):
        return _digest({"universe": self.universe_id, "source": self.source_namespace,
                        "reference": self.source_reference, "captured_at": self.captured_at,
                        "raw": self.content_sha256})


def parse_manifest(*, body, universe_id, name, source_reference, captured_at,
                   source_namespace="user_manifest", source_label_date=None):
    if not isinstance(body, bytes) or not body or len(body) > MAX_BYTES:
        raise ResourceLimitError("Collection manifest byte bound exceeded")
    if not isinstance(universe_id, str) or not _ID.fullmatch(universe_id):
        raise ValidationError("Invalid collection universe ID")
    if not isinstance(source_namespace, str) or not _ID.fullmatch(source_namespace):
        raise ValidationError("Invalid collection source namespace")
    _text(name, "name")
    _text(source_reference, "source reference", 1024)
    reference = PurePosixPath(source_reference)
    if reference.is_absolute() or ".." in reference.parts or "\\" in source_reference or ":" in source_reference:
        raise ValidationError("Collection reference must be a private relative path")
    if source_label_date is not None:
        try:
            if not isinstance(source_label_date, str) or date.fromisoformat(source_label_date).isoformat() != source_label_date:
                raise ValueError()
        except ValueError as exc:
            raise ValidationError("Invalid collection date label") from exc
    captured_at = _utc(captured_at)
    try:
        reader = csv.reader(io.StringIO(body.decode("utf-8-sig"), newline=""), strict=True)
        header = next(reader)
        if (not 2 <= len(header) <= 128 or len(set(header)) != len(header)
            or "Symbol" not in header or "Description" not in header
            or any(not h or len(h)>256 or "\x00" in h for h in header)):
            raise ValidationError("Collection CSV header is invalid")
        members = []
        seen = set()
        for number, cells in enumerate(reader, start=2):
            if len(members) >= MAX_MEMBERS:
                raise ResourceLimitError("Collection membership exceeds 5000")
            if len(cells) != len(header) or any(len(v)>16384 or "\x00" in v for v in cells):
                raise ValidationError("Collection CSV row shape is invalid")
            row = dict(zip(header,cells))
            symbol = row["Symbol"]
            if not _SYMBOL.fullmatch(symbol) or symbol in seen:
                raise ValidationError("Collection contains an invalid or duplicate source symbol")
            _text(row["Description"], "member name", 4096)
            seen.add(symbol)
            members.append(ManifestMember(symbol, row["Description"], number, dumps_strict(row)))
    except (UnicodeError, csv.Error, StopIteration) as exc:
        raise ValidationError("Collection manifest is not valid UTF-8 CSV") from exc
    if not members:
        raise ValidationError("Collection manifest is empty")
    members = tuple(sorted(members, key=lambda r:r.symbol))
    state = {"version": VERSION, "universe": universe_id, "name": name,
             "namespace": source_namespace, "label_date": source_label_date,
             "members": [loads_strict(m.payload_json) for m in members]}
    return CollectionManifest(universe_id,name,source_namespace,source_reference,source_label_date,
        captured_at,body,members,_digest(state),_digest([m.symbol for m in members]))


class CollectionManifestPublisher:
    def __init__(self, stores: StoreMap, registry):
        if not isinstance(stores, StoreMap):
            raise ValidationError("Explicit collection store map required")
        if not {EVIDENCE,MEMBERSHIP}.issubset({d.id for d in registry.datasets_for("market")}):
            raise ValidationError("Collection datasets are not registered")
        self.stores = stores
        self.coordinator = IngestionCoordinator(stores, code_version=VERSION)

    def publish(self, manifest):
        if not isinstance(manifest, CollectionManifest):
            raise ValidationError("Expected a parsed collection manifest")
        checked = parse_manifest(body=manifest.body, universe_id=manifest.universe_id,
            name=manifest.name, source_namespace=manifest.source_namespace,
            source_reference=manifest.source_reference, captured_at=manifest.captured_at,
            source_label_date=manifest.source_label_date)
        if checked != manifest:
            raise ValidationError("Collection prepared manifest changed")
        warnings = ("source_symbols_not_provider_mappings", "filename_date_not_historical_availability")
        with acquire_write_session(self.stores,(StoreRole.MARKET,)) as locks:
            with quiet_immutable_read_connection(self.stores,"market") as c:
                universe = c.execute("SELECT name,source_namespace FROM market_collection_universes WHERE universe_id=?",
                                     (manifest.universe_id,)).fetchone()
                if universe and tuple(universe) != (manifest.name,manifest.source_namespace):
                    raise ConflictError("Collection universe definition changed")
                previous = c.execute("SELECT s.* FROM market_collection_heads h JOIN market_collection_snapshots s ON s.snapshot_id=h.snapshot_id WHERE h.universe_id=?",
                                     (manifest.universe_id,)).fetchone()
                replay = c.execute("SELECT semantic_identity,state_sha256 FROM market_collection_snapshots WHERE acquisition_identity=?",
                                   (manifest.acquisition_identity,)).fetchone()
            if replay and replay["state_sha256"] != manifest.state_sha256:
                raise ConflictError("Collection acquisition identity changed meaning")
            if replay or (previous and previous["state_sha256"] == manifest.state_sha256):
                semantic = replay["semantic_identity"] if replay else previous["semantic_identity"]
                return IngestionReceipt("unchanged","market",MEMBERSHIP,semantic,None,None,None,0,warnings)
            if previous and manifest.captured_at <= previous["captured_at"]:
                raise ConflictError("Collection transitions need increasing capture time")
            predecessor = previous["snapshot_id"] if previous else None
            sequence = previous["version_sequence"]+1 if previous else 1
            scope = {"universe_id":manifest.universe_id, "source_namespace":manifest.source_namespace,
                     "source_label_date":manifest.source_label_date}
            semantic = _digest({"version":VERSION,"scope":scope,"state":manifest.state_sha256,"predecessor":predecessor})
            snapshot_id = stable_id("collection_snapshot",semantic)
            artifact_id = stable_id("collection_artifact",snapshot_id,manifest.content_sha256)
            def writer(c,rid):
                if not universe:
                    c.execute("INSERT INTO market_collection_universes VALUES (?,?,?,?)",
                              (manifest.universe_id,manifest.name,manifest.source_namespace,rid))
                c.execute("INSERT INTO market_collection_artifacts VALUES (?,?,?,?,?,?,?,?)",
                          (artifact_id,manifest.content_sha256,"text/csv",manifest.body,len(manifest.body),
                           manifest.source_reference,manifest.captured_at,rid))
                c.execute("INSERT INTO market_collection_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (snapshot_id,manifest.universe_id,artifact_id,manifest.acquisition_identity,
                           semantic,manifest.state_sha256,manifest.membership_sha256,manifest.source_label_date,
                           manifest.captured_at,sequence,predecessor,len(manifest.members),rid))
                c.executemany("INSERT INTO market_collection_members VALUES (?,?,?,?,?)",
                    [(snapshot_id,m.symbol,m.source_row,m.name,m.payload_json) for m in manifest.members])
                if previous:
                    c.execute("UPDATE market_collection_heads SET snapshot_id=? WHERE universe_id=?",
                              (snapshot_id,manifest.universe_id))
                else:
                    c.execute("INSERT INTO market_collection_heads VALUES (?,?)",(manifest.universe_id,snapshot_id))
                artifact = ArtifactWrite(artifact_id,EVIDENCE,manifest.content_sha256,"text/csv",
                    len(manifest.body),manifest.source_reference,scope,manifest.captured_at,"datetime",VERSION)
                snapshot = SnapshotWrite(snapshot_id,MEMBERSHIP,semantic,scope,"complete",len(manifest.members),
                    manifest.captured_at,"datetime","validated",(artifact_id,),warnings)
                return WriteResult(len(manifest.members)+3+(not universe),(artifact,),snapshot,(),warnings=warnings)
            return self.coordinator.execute(role="market",dataset_id=MEMBERSHIP,output_dataset_ids=(EVIDENCE,MEMBERSHIP),
                semantic_identity=semantic,run_id=stable_id("collection_run",snapshot_id),
                command="market.collection_manifest",scope=scope,started_at=manifest.captured_at,
                completed_at=manifest.captured_at,fetched_count=len(manifest.members),writer=writer,held_locks=locks)


@dataclass(frozen=True)
class CollectionSelection:
    universe_id: str
    snapshot_id: str
    captured_at: str
    membership_sha256: str
    members: tuple[ManifestMember,...]


def read_collection(stores, universe_id, *, cutoff=None):
    if not isinstance(universe_id,str) or not _ID.fullmatch(universe_id):
        raise ValidationError("Invalid collection universe ID")
    normalized_cutoff = _utc(cutoff) if cutoff is not None else None
    with quiet_immutable_read_connection(stores,"market") as c:
        if normalized_cutoff is None:
            row=c.execute("SELECT s.* FROM market_collection_heads h JOIN market_collection_snapshots s ON s.snapshot_id=h.snapshot_id WHERE h.universe_id=?",(universe_id,)).fetchone()
        else:
            row=c.execute("SELECT * FROM market_collection_snapshots WHERE universe_id=? AND captured_at<=? ORDER BY captured_at DESC,version_sequence DESC LIMIT 1",(universe_id,normalized_cutoff)).fetchone()
        if row is None:
            return None
        members=tuple(ManifestMember(r["source_symbol"],r["display_name"],r["source_row"],r["source_payload_json"])
            for r in c.execute("SELECT * FROM market_collection_members WHERE snapshot_id=? ORDER BY source_symbol LIMIT ?",
                               (row["snapshot_id"],MAX_MEMBERS+1)))
        if len(members)!=row["member_count"] or len(members)>MAX_MEMBERS:
            raise ConflictError("Collection snapshot membership is incomplete")
        if _digest([m.symbol for m in members])!=row["membership_sha256"]:
            raise ConflictError("Collection membership digest differs")
        return CollectionSelection(universe_id,row["snapshot_id"],row["captured_at"],row["membership_sha256"],members)
