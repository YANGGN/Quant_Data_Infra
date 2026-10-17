"""Bounded extractive descriptions with immutable profile evidence and replay."""
from __future__ import annotations
import hashlib
import json
import re
from collections.abc import Sequence
from datetime import datetime, timezone
from ..contracts import IngestionReceipt
from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..ingestion import ArtifactWrite, IngestionCoordinator, SnapshotWrite, WriteResult
from ..json_codec import dumps_strict, loads_strict
from ..market.collection_mappings import identity_source_object
from ..market.collection_universe import _utc
from ..stores import acquire_write_session, quiet_immutable_read_connection, stable_id
from .short_description_registry import DATASET, EVIDENCE

VERSION = "company_short_descriptions.v1"
MAX_MEMBERS = 2248
MAX_BODY = 1048576
MAX_TEXT = 400
WARNINGS = ("derived_extractive_description", "local_capture_not_historical_publication")
IDENTITY_KEYS = ("source_symbol", "provider_symbol", "instrument_id", "cik",
                 "membership_snapshot_id", "mapping_id")
_ABBREVIATIONS = frozenset(("inc", "corp", "co", "ltd", "plc", "s.a", "n.v", "u.s", "u.k",
                           "u.s.a", "l.p", "l.l.c", "llc", "b.v", "st", "dr", "mr", "mrs"))

def digest(value):
    if not isinstance(value, bytes):
        value = dumps_strict(value).encode()
    return hashlib.sha256(value).hexdigest()

def utcnow():
    return _utc(datetime.now(timezone.utc).isoformat())

def short_description(profile):
    """Extract one or two opening business sentences, preserving source wording."""
    description = profile.get("description")
    if description is not None and not isinstance(description, str):
        raise ValidationError("Profile description is not text")
    text = re.sub(r"\s+", " ", description or "").strip()
    if not text:
        industry = profile.get("industry")
        name = profile.get("companyName")
        if not isinstance(industry, str) or not industry.strip() or not isinstance(name, str) or not name.strip():
            raise ValidationError("Profile supplies neither business description nor company industry")
        text = re.sub(r"\s+", " ", name).strip() + " operates in the " + re.sub(r"\s+", " ", industry).strip() + " industry."
        method = "industry_template.v1"
    else:
        method = "opening_sentence_excerpt.v1"
        sentence_count = 0
        for match in re.finditer(r'[.!?](?=\s+[A-Z0-9\"\u201c]|$)', text):
            token = text[:match.end()].split()[-1].strip('"”').lower().rstrip(".")
            if text[match.start()] == "." and (token in _ABBREVIATIONS or re.fullmatch(r"(?:[a-z]\.)+[a-z]?", token)):
                continue
            # Avoid extracting only a company name ending in an unfamiliar abbreviation.
            if len(text[:match.end()].split()) < 7:
                continue
            sentence_count += 1
            if sentence_count == 1 and len(text[:match.end()].split()) < 12 and match.end() < len(text):
                continue
            text = text[:match.end()]
            break
    truncated = len(text) > MAX_TEXT
    if truncated:
        prefix = text[:MAX_TEXT - 3]
        if " " in prefix:
            prefix = prefix.rsplit(" ", 1)[0]
        text = prefix.rstrip(" ,;:.") + "..."
    if not text or any(ord(c) < 32 for c in text):
        raise ValidationError("Invalid short description")
    return text, method, truncated

def selected_inputs(stores, *, cutoff):
    """Read the frozen selected FMP mappings and their original profile evidence."""
    cutoff = _utc(cutoff)
    with quiet_immutable_read_connection(stores, "market") as c:
        head = c.execute("""SELECT s.* FROM market_collection_heads h JOIN market_collection_snapshots s
            ON s.snapshot_id=h.snapshot_id WHERE h.universe_id='major_index_liquid'""").fetchone()
        if head is None or head["captured_at"] > cutoff or not 1 <= head["member_count"] <= MAX_MEMBERS:
            raise ConflictError("Selected membership is unavailable or exceeds scope")
        rows = c.execute("""SELECT m.*, s.captured_at AS mapping_captured_at,
            e.raw_body,e.source_reference,e.captured_at AS source_captured_at,e.evidence_sha256 AS source_sha256
            FROM market_collection_mapping_heads h
            JOIN market_collection_mapping_snapshots s ON s.mapping_id=h.mapping_id
            JOIN market_collection_provider_mappings m ON m.mapping_id=h.mapping_id
            LEFT JOIN market_collection_identity_evidence e ON e.evidence_id=m.evidence_id
            WHERE h.membership_snapshot_id=? AND h.provider='fmp' ORDER BY m.source_symbol""",
            (head["snapshot_id"],)).fetchall()
        if len(rows) != head["member_count"]:
            raise ConflictError("Selected mapping is incomplete")
        output = []
        for row in rows:
            if row["status"] != "resolved" or not row["instrument_id"] or row["mapping_captured_at"] > cutoff:
                raise ConflictError("Selected ticker lacks an available resolved FMP identity")
            member = {key: row[key] for key in IDENTITY_KEYS}
            member["source"] = None
            if row["raw_body"] is not None:
                body = bytes(row["raw_body"])
                if digest(body) != row["source_sha256"] or row["source_captured_at"] > cutoff:
                    raise ConflictError("Retained identity evidence differs or is future")
                obj = identity_source_object(loads_strict(body, max_bytes=MAX_BODY), row["evidence_pointer"], "fmp")
                if isinstance(obj.get("description"), str) and obj["description"].strip():
                    member["source"] = {"raw_body": body.decode("utf-8"),
                        "source_pointer": row["evidence_pointer"], "source_reference": row["source_reference"],
                        "captured_at": row["source_captured_at"], "origin": "retained_fmp_identity",
                        "source_evidence_id": row["evidence_id"]}
            output.append(member)
        return output

def prepare_record(member, source):
    if set(member) != set(IDENTITY_KEYS) | {"source"}:
        raise ValidationError("Description member fields differ")
    if any(not isinstance(member[k], str) or not member[k] for k in IDENTITY_KEYS if k != "cik"):
        raise ValidationError("Description requires exact selected identity")
    required = {"raw_body", "source_pointer", "source_reference", "captured_at", "origin", "source_evidence_id"}
    if not isinstance(source, dict) or set(source) != required:
        raise ValidationError("Description source fields differ")
    if source["origin"] not in ("retained_fmp_identity", "fmp_profile"):
        raise ValidationError("Unsupported description source")
    body = source["raw_body"].encode("utf-8")
    if not 1 <= len(body) <= MAX_BODY:
        raise ResourceLimitError("Profile body exceeds bound")
    reference = source["source_reference"]
    if not isinstance(reference, str) or not reference or reference.startswith("/") or ".." in reference.split("/") or ":" in reference or "\\" in reference:
        raise ValidationError("Profile reference must be a private relative path")
    source = dict(source, captured_at=_utc(source["captured_at"]))
    profile = identity_source_object(loads_strict(body, max_bytes=MAX_BODY), source["source_pointer"], "fmp")
    if profile.get("symbol") != member["provider_symbol"]:
        raise ConflictError("Profile symbol differs from the pinned provider mapping")
    cik = profile.get("cik")
    if cik is not None and str(cik).strip() not in ("", "0"):
        if not str(cik).isdigit() or len(str(cik)) > 10:
            raise ValidationError("Invalid profile CIK")
        if member["cik"] is not None and str(cik).zfill(10) != member["cik"]:
            raise ConflictError("Profile CIK differs from the pinned identity")
    text, method, truncated = short_description(profile)
    meaning = {key: member[key] for key in IDENTITY_KEYS}
    meaning.update(full_description=profile.get("description"), short_description=text,
                   method=method, excerpt_truncated=int(truncated))
    semantic = digest(meaning)
    evidence_id = stable_id("company_profile", digest({**source, "raw_body": digest(body)}))
    return {"member": member, "source": source, **meaning, "semantic_identity": semantic,
            "evidence_id": evidence_id, "content_sha256": digest(body)}

class DescriptionPublisher:
    def __init__(self, stores, registry):
        if not {DATASET, EVIDENCE} <= {d.id for d in registry.datasets_for("company")}:
            raise ValidationError("Description datasets are not registered")
        self.stores, self.registry = stores, registry
        self.coordinator = IngestionCoordinator(stores, code_version=VERSION)

    def publish(self, records, *, published_at):
        published = _utc(published_at)
        if not isinstance(records, Sequence) or not 1 <= len(records) <= MAX_MEMBERS:
            raise ResourceLimitError("Description batch exceeds selected scope")
        if len({r["instrument_id"] for r in records}) != len(records):
            raise ConflictError("Description batch repeats an instrument")
        for row in records:
            if prepare_record(row["member"], row["source"]) != row or row["source"]["captured_at"] > published:
                raise ConflictError("Description changed or predates its source")
        batch_semantic = digest(sorted(r["semantic_identity"] for r in records))
        with acquire_write_session(self.stores, ("market", "company"), timeout_seconds=30) as locks:
            current = {r["source_symbol"]: r for r in selected_inputs(self.stores, cutoff=published)}
            for row in records:
                member = current.get(row["source_symbol"])
                if member is None or any(member[k] != row[k] for k in IDENTITY_KEYS):
                    raise ConflictError("Pinned description mapping changed")
                if row["source"]["origin"] == "retained_fmp_identity" and member["source"] != row["source"]:
                    raise ConflictError("Retained profile evidence changed")
            with quiet_immutable_read_connection(self.stores, "company") as c:
                ledger = [tuple(r) for r in c.execute("SELECT migration_id,sha256 FROM schema_migrations ORDER BY ordinal")]
                if ledger != [(m.id, m.sha256) for m in self.registry.migrations_for("company")]:
                    raise ConflictError("Description migration ledger differs")
                changes = []
                for row in records:
                    old = c.execute("SELECT * FROM company_short_descriptions WHERE instrument_id=?", (row["instrument_id"],)).fetchone()
                    if old and old["content_identity"] == row["semantic_identity"]:
                        continue
                    if old and old["available_at"] >= published:
                        raise ConflictError("Description revision cannot be backdated or tied")
                    existing = c.execute("SELECT 1 FROM company_profile_evidence WHERE evidence_id=?", (row["evidence_id"],)).fetchone()
                    changes.append((row, dict(old) if old else None, bool(existing)))
            if not changes:
                return IngestionReceipt(outcome="unchanged", store="company", dataset_id=DATASET,
                    semantic_identity=batch_semantic, run_id=None, artifact_id=None, snapshot_id=None,
                    written_count=0, warnings=WARNINGS)
            scope = {"membership_snapshot_id": records[0]["membership_snapshot_id"],
                     "symbols": sorted(r["source_symbol"] for r, _, _ in changes), "method_version": VERSION}
            # Predecessors distinguish a genuine A -> B -> A revision from replay.
            semantic = digest([(r["semantic_identity"], old["version_id"] if old else None) for r, old, _ in changes])
            def writer(c, run_id):
                artifacts, count = [], 0
                for row, old, existing in changes:
                    source = row["source"]; raw = source["raw_body"].encode("utf-8")
                    if not existing:
                        c.execute("INSERT INTO company_profile_evidence VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (row["evidence_id"], row["content_sha256"], raw, source["source_reference"],
                             source["source_pointer"], source["origin"], source["source_evidence_id"],
                             row["provider_symbol"], source["captured_at"], run_id))
                        count += 1
                    version_id = stable_id("company_description", row["semantic_identity"], old["version_id"] if old else "")
                    c.execute("INSERT INTO company_short_description_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (version_id, digest([row["semantic_identity"], old["version_id"] if old else None]),
                         row["semantic_identity"], row["source_symbol"], row["instrument_id"], row["cik"], row["membership_snapshot_id"],
                         row["mapping_id"], row["evidence_id"], row["full_description"], row["short_description"],
                         row["method"], row["excerpt_truncated"], published,
                         1 if old is None else old["version_sequence"]+1, old["version_id"] if old else None, run_id))
                    count += 1
                    artifacts.append(ArtifactWrite(stable_id("profile_artifact", semantic, row["evidence_id"]), EVIDENCE,
                        row["content_sha256"], "application/json", len(raw), source["source_reference"],
                        {"provider_symbol": row["provider_symbol"], "source_pointer": source["source_pointer"]},
                        source["captured_at"], "datetime", VERSION))
                snapshot = SnapshotWrite(stable_id("descriptions_snapshot", semantic), DATASET, semantic, scope,
                    "partial", len(changes), published, "datetime", "validated",
                    tuple(a.artifact_id for a in artifacts), WARNINGS)
                return WriteResult(count, tuple(artifacts), snapshot, (), warnings=WARNINGS)
            return self.coordinator.execute(role="company", dataset_id=DATASET,
                output_dataset_ids=(EVIDENCE, DATASET), semantic_identity=semantic,
                run_id=stable_id("descriptions_run", semantic), command="company.short_descriptions.populate",
                scope=scope, started_at=published, completed_at=published, fetched_count=0,
                writer=writer, held_locks=locks)

def read_descriptions(stores, symbols, *, as_of):
    if not isinstance(symbols, (list, tuple)) or not 1 <= len(symbols) <= MAX_MEMBERS or len(set(symbols)) != len(symbols):
        raise ValidationError("Description symbols must be a bounded unique list")
    cutoff = _utc(as_of)
    with acquire_write_session(stores, ("company",), timeout_seconds=2):
        with quiet_immutable_read_connection(stores, "company") as c:
            result = []
            for symbol in symbols:
                row = c.execute("""SELECT d.* FROM company_short_description_versions d
                    WHERE source_symbol=? AND available_at<=? ORDER BY version_sequence DESC LIMIT 1""",
                    (symbol, cutoff)).fetchone()
                result.append(dict(row) if row else {"source_symbol": symbol, "short_description": None,
                                                    "missing_reason": "not_established"})
            return result
