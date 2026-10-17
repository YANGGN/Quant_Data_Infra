"""Read-only agent projection of canonical FMP classification versions."""
from contextlib import contextmanager
from dataclasses import replace
import sqlite3

from quant_data.contracts import LineageRef, TruncationV1, WarningV1
from quant_data.errors import ConflictError, ResourceLimitError, StoreUnavailableError, ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.market.classification_registry import DATASET
from quant_data.market.classifications import UNIVERSE, _label
from quant_data.stores import acquire_write_session, quiet_immutable_read_connection
from .classification_contracts import TOOL, MAX_TICKERS, MAX_RECORDS, ClassificationArguments, parse
from .retained_research_common import cutoff, digest, instant, record
from .results import QueryResult, research_envelope

COLUMNS = ("version_id", "content_identity", "instrument_id", "source_symbol", "provider_symbol",
    "classification_provider", "sector", "industry", "membership_snapshot_id", "mapping_id", "source_cik",
    "source_mapping_id", "source_description_version_id", "source_evidence_id", "source_content_sha256",
    "source_captured_at", "available_at", "version_sequence", "predecessor_version_id", "run_id")
SELECT = "SELECT " + ",".join("v." + k for k in COLUMNS) + " FROM market_classification_versions v "


@contextmanager
def connection(context, registry):
    context.checkpoint()
    interrupted = []
    steps = 0
    quantum = min(1000, context.budget.max_operations)
    def progress():
        nonlocal steps
        steps += quantum
        try:
            context.checkpoint()
            context.budget.require(operations=steps)
        except Exception as exc:
            interrupted.append(exc)
            return 1
        return 0
    try:
        with acquire_write_session(context.store_map, ("market",), timeout_seconds=1):
            with quiet_immutable_read_connection(context.store_map, "market") as c:
                c.set_progress_handler(progress, quantum)
                try:
                    actual = [tuple(r) for r in c.execute("SELECT migration_id,store_role,ordinal,resource,sha256,reconstruction_state FROM schema_migrations ORDER BY ordinal")]
                    expected = [(m.id, m.store, m.ordinal, m.resource, m.sha256, m.reconstruction_state)
                                for m in sorted(registry.migrations_for("market"), key=lambda m: m.ordinal)]
                    if actual != expected:
                        raise StoreUnavailableError("Classification store migration ledger differs")
                    declaration = next(d for d in registry.datasets_for("market") if d.id == DATASET)
                    row = c.execute("""SELECT d.store_role,d.layer,d.schema_version,d.relations_json,d.active,
                        i.identity_sha256,i.registered_version FROM dataset_registry d
                        JOIN dataset_identity_contracts i ON i.dataset_id=d.dataset_id WHERE d.dataset_id=?""", (DATASET,)).fetchone()
                    if row is None or tuple(row) != ("market", declaration.layer, declaration.schema_version,
                        dumps_strict(list(declaration.relations)), int(declaration.active), declaration.identity_sha256, declaration.version):
                        raise StoreUnavailableError("Classification dataset declaration differs")
                    yield c
                finally:
                    c.set_progress_handler(None, 0)
                    if interrupted: raise interrupted[0]
    except sqlite3.Error as exc:
        raise StoreUnavailableError("The canonical FMP classification store could not be read") from exc
    except (OSError, ConflictError) as exc:
        raise StoreUnavailableError("The canonical FMP classification store is missing or busy") from exc
    context.checkpoint()


def _validate(row, at):
    value = dict(row)
    if (value["classification_provider"] != "fmp"
        or not instant(value["source_captured_at"]) <= instant(value["available_at"]) <= at):
        raise StoreUnavailableError("Classification provider or availability differs")
    _label(value["sector"]); _label(value["industry"])
    identity = {k: value[k] for k in ("source_symbol", "provider_symbol", "instrument_id", "classification_provider", "sector", "industry")}
    if digest(identity) != value["content_identity"]:
        raise StoreUnavailableError("Classification semantic identity differs")
    return value


def invoke(arguments, context, registry):
    if not isinstance(arguments, ClassificationArguments):
        raise ValidationError("Classifications require registered typed arguments")
    public = {k: list(v) if isinstance(v, tuple) else v for k, v in dict(arguments).items()}
    args = dict(parse(public))
    at = cutoff(args, context)
    found, missing = [], []
    with connection(context, registry) as c:
        if "tickers" in args:
            for ticker in args["tickers"]:
                context.checkpoint()
                row = c.execute(SELECT + "WHERE v.source_symbol=? AND v.available_at<=? ORDER BY v.available_at DESC,v.version_sequence DESC LIMIT 1", (ticker, at)).fetchone()
                if row is None: missing.append(ticker)
                else: found.append(_validate(row, at))
        else:
            rows = c.execute(SELECT + """WHERE v.available_at<=? AND NOT EXISTS (
                SELECT 1 FROM market_classification_versions n WHERE n.instrument_id=v.instrument_id
                AND n.version_sequence>v.version_sequence AND n.available_at<=?)
                ORDER BY v.source_symbol LIMIT ?""", (at, at, MAX_TICKERS + 1)).fetchall()
            if len(rows) > MAX_TICKERS:
                raise ResourceLimitError("Saved classification population exceeds 2248; use ticker batches")
            for row in rows:
                context.checkpoint()
                found.append(_validate(row, at))
    matched = [r for r in found if ("sectors" not in args or r["sector"] in args["sectors"])
               and ("industries" not in args or r["industry"] in args["industries"])]
    records = []
    for value in matched:
        records.append(record("instrument_classification", dict(value, ticker=value["source_symbol"], missing_reason=None)))
    for ticker in missing:
        records.append(record("classification_missing", dict(ticker=ticker, classification_provider="fmp",
            sector=None, industry=None, missing_reason="no_classification_at_cutoff")))
    records.sort(key=lambda r: next(f.value for f in r.fields if f.name == "ticker"))
    source = dict(classification_provider="fmp", dataset_id=DATASET, universe_id=UNIVERSE,
        universe_basis="retained_classification_population", selection="ticker_batch" if "tickers" in args else "full_universe",
        as_of=at, availability_basis="local_classification_publication", historical_effective_date="not_established",
        requested_ticker_count=len(args["tickers"]) if "tickers" in args else None,
        available_classification_count=len(found), matched_count=len(matched), missing_count=len(missing),
        filtered_out_count=len(found)-len(matched), sectors=args.get("sectors", ()), industries=args.get("industries", ()),
        canonical_store="market", evidence_store="company", lineage_location="instrument_classification_records", provider_requests=0)
    records.insert(0, record("classification_source", source))
    context.checkpoint()
    context.budget.require(rows=len(records))
    # Per-row versions and original company evidence are retained in records;
    # one aggregate reference keeps full-universe responses inside lineage bounds.
    refs = (LineageRef(dataset_id=DATASET, store_role="market", semantic_id=digest([r.to_primitive() for r in records])),)
    envelope = research_envelope(TOOL, dict(as_of=at, parameters=[dict(name="query_json", value=dumps_strict(args))]), (), refs,
        unsafe_reasons=("historical_effective_date_not_established",))
    contract = replace(envelope.contract,
        temporal=replace(envelope.contract.temporal, availability_basis="local_classification_publication"),
        availability_policy="local_classification_publication", vintage_policy="latest_version_at_publication_cutoff",
        execution_policy="host_selected_market_immutable_read", sample={"matched_count": len(matched), "missing_count": len(missing)})
    material = contract.to_primitive(); material.pop("analysis_id")
    envelope = replace(envelope, contract=replace(contract, analysis_id=digest(material)))
    return QueryResult(tool=TOOL, status="ok" if matched else "not_established", records=tuple(records), lineage=refs,
        warnings=(WarningV1("fmp_classification_not_official_gics", "Sector and industry labels use only FMP's classification; they are not official GICS."),
            WarningV1("historical_effective_date_not_established", "The cutoff uses local classification publication time and does not establish historical economic classifications or index membership.")),
        truncation=TruncationV1(False, MAX_RECORDS, len(records), len(records), False), research_contract=envelope)
