"""Validate an already-pinned collection without silently selecting newer heads."""
from .collection_bindings import (PinnedCollection,BoundSubject,RetainedInstrument,BINDING_IDS,
    EXPECTED_PROVIDERS,REQUIRED_FIELDS)
from .collection_universe import MAX_MEMBERS,_utc,_digest
from ..errors import ConflictError,ValidationError,ResourceLimitError
from ..stores import quiet_immutable_read_connection

def validate_pinned_collection(stores,selection,*,cutoff):
    if not isinstance(selection,PinnedCollection):raise ValidationError("A pinned collection is required")
    b=selection.binding;at=_utc(cutoff)
    expected_retained=("curated_etfs","major_indexes") if b.id in ("daily_prices","news") else ()
    if (b.id not in BINDING_IDS or b.provider!=EXPECTED_PROVIDERS[b.id]
        or b.required_identity_fields!=REQUIRED_FIELDS[b.id] or b.retain_universes!=expected_retained
        or b.universe_id!="major_index_liquid" or b.mode not in ("prepared","active")):
        raise ValidationError("Pinned collection binding differs")
    with quiet_immutable_read_connection(stores,"market") as c:
        membership=c.execute("SELECT * FROM market_collection_snapshots WHERE snapshot_id=?",
            (selection.membership_snapshot_id,)).fetchone()
        if membership is None or membership["universe_id"]!=b.universe_id or _utc(membership["captured_at"])>at:
            raise ConflictError("Pinned membership is missing, different or from the future")
        members={r[0] for r in c.execute("SELECT source_symbol FROM market_collection_members WHERE snapshot_id=? LIMIT ?",
            (selection.membership_snapshot_id,MAX_MEMBERS+1))}
        if len(members)!=membership["member_count"]:raise ConflictError("Pinned membership is incomplete")
        mapping=c.execute("SELECT * FROM market_collection_mapping_snapshots WHERE mapping_id=?",(selection.mapping_id,)).fetchone()
        if (mapping is None or mapping["membership_snapshot_id"]!=selection.membership_snapshot_id
            or mapping["provider"]!=b.provider or _utc(mapping["captured_at"])>at):
            raise ConflictError("Pinned provider mapping is missing, different or from the future")
        records=c.execute("SELECT * FROM market_collection_provider_mappings WHERE mapping_id=? ORDER BY source_symbol LIMIT ?",
            (selection.mapping_id,MAX_MEMBERS+1)).fetchall()
        if len(records)!=mapping["member_count"] or {r["source_symbol"] for r in records}!=members:
            raise ConflictError("Pinned provider mapping is incomplete")
        subjects=[]
        for r in records:
            missing=[field for field in b.required_identity_fields if r[field] is None]
            state="identity_incomplete" if r["status"]=="resolved" and missing else r["status"]
            reason="Missing "+", ".join(missing) if state=="identity_incomplete" else r["reason"]
            subjects.append(BoundSubject(r["source_symbol"],r["provider_symbol"],r["provider_subject"],
                r["instrument_id"],r["cik"],state,reason))
        retained=[]
        snapshots=sorted({r.universe_snapshot_id for r in selection.retained})
        universes=set()
        for snapshot_id in snapshots:
            snapshot=c.execute("SELECT * FROM stage10_universe_snapshots WHERE universe_snapshot_id=?",(snapshot_id,)).fetchone()
            if (snapshot is None or snapshot["universe_id"] not in expected_retained
                or snapshot["universe_id"] in universes or _utc(snapshot["captured_at"])>at
                or snapshot["completeness"]!="complete"):
                raise ConflictError("Pinned independent universe is missing, incomplete or from the future")
            universes.add(snapshot["universe_id"])
            records=c.execute("""SELECT i.instrument_id,i.provider_symbol,i.asset_type
                FROM stage10_universe_snapshot_members m JOIN stage10_instruments i ON i.instrument_id=m.instrument_id
                WHERE m.universe_snapshot_id=? ORDER BY i.instrument_id LIMIT 801""",(snapshot_id,)).fetchall()
            if len(records)!=snapshot["member_count"] or len(records)>800:
                raise ConflictError("Pinned independent universe members differ")
            asset="etf" if snapshot["universe_id"]=="curated_etfs" else "index"
            if any(r["asset_type"]!=asset for r in records):raise ConflictError("Pinned independent asset scope differs")
            retained.extend(RetainedInstrument(r["instrument_id"],r["provider_symbol"],r["asset_type"],snapshot_id) for r in records)
        if universes!=set(expected_retained):
            raise ConflictError("Pinned selection lacks explicit independent universe snapshots")
    material={"config":b.config_sha256,"binding":b.id,"membership":selection.membership_snapshot_id,
        "mapping":selection.mapping_id,"retained_snapshots":snapshots}
    # pin_binding preserves configured universe order; compare membership as a
    # set here without changing the original run's tuple or digest.
    if (tuple(subjects)!=selection.subjects or len(retained)!=len(selection.retained)
        or set(retained)!=set(selection.retained) or _digest(material)!=selection.scope_sha256):
        raise ConflictError("Pinned selection contents or digest differ from retained evidence")
    return selection
