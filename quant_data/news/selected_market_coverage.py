"""Pinned news matching with separate Alpaca symbols and retained ETF mappings."""
from dataclasses import dataclass
import csv,io
from ..errors import ConflictError,ResourceLimitError,StoreUnavailableError,ValidationError
from ..market.collection_bindings import pin_binding,load_bindings
from ..market.collection_universe import _utc,_digest,parse_manifest
from ..operations.collection_targets import selected_market_rows,validate_selection
from ..stores import quiet_immutable_read_connection
from .current_market_coverage import CurrentMarketNewsInstrument

RETAINED_ETF_UNIVERSE="retained_news_etfs"
# Operational grouping is independent of the provider's 50-article page size.
SELECTED_ALPACA_NEWS_BATCH_SIZE = 10
SELECTED_ALPACA_NEWS_MAX_BATCHES = 6600 // SELECTED_ALPACA_NEWS_BATCH_SIZE

@dataclass(frozen=True)
class SelectedNewsCoverage:
    instruments: tuple[CurrentMarketNewsInstrument,...]
    sha256: str
    membership_snapshot_id: str
    fmp_mapping_id: str
    alpaca_mapping_id: str | None
    retained_alpaca_mapping_id: str | None
    alpaca_symbols: tuple[str,...]
    mapping_gaps: tuple[tuple[str,str],...]
    @property
    def all_symbols(self):return tuple(i.symbol for i in self.instruments)
    @property
    def unsupported_index_symbols(self):return tuple(i.symbol for i in self.instruments if i.asset_type=="index")
    @property
    def alpaca_symbol_batches(self):
        return tuple(self.alpaca_symbols[i:i + SELECTED_ALPACA_NEWS_BATCH_SIZE]
                     for i in range(0, len(self.alpaca_symbols), SELECTED_ALPACA_NEWS_BATCH_SIZE))
    def report(self):
        return {"selection_sha256":self.sha256,"matching_symbols":len(self.instruments),
            "alpaca_symbols":len(self.alpaca_symbols),"alpaca_batches":len(self.alpaca_symbol_batches),
            "symbols_per_batch_limit":SELECTED_ALPACA_NEWS_BATCH_SIZE,
            "mapping_gaps":[{"symbol":s,"reason":r} for s,r in self.mapping_gaps],
            "unsupported_index_symbols":list(self.unsupported_index_symbols),
            "global_feed_multiplier":1,"historical_backfill":False}

def _mapping(c,mapping_id,cutoff):
    if not isinstance(mapping_id,str) or not mapping_id or len(mapping_id)>256:
        raise ValidationError("News mapping identifier is invalid")
    meta=c.execute("SELECT * FROM market_collection_mapping_snapshots WHERE mapping_id=?",(mapping_id,)).fetchone()
    if meta is None or meta["provider"]!="alpaca" or _utc(meta["captured_at"])>_utc(cutoff):
        raise ConflictError("News provider mapping is absent, future, or belongs to another provider")
    membership=c.execute("SELECT captured_at FROM market_collection_snapshots WHERE snapshot_id=?",
        (meta["membership_snapshot_id"],)).fetchone()
    if membership is None or _utc(membership[0])>_utc(cutoff):
        raise ConflictError("News provider membership is from the future")
    rows=tuple(c.execute("SELECT * FROM market_collection_provider_mappings WHERE mapping_id=? ORDER BY source_symbol LIMIT 5001",
        (mapping_id,)))
    if len(rows)>5000 or len(rows)!=meta["member_count"]:
        raise ResourceLimitError("News mapping is incomplete or exceeds its bound")
    return meta,rows

def read_selected_news_coverage(stores,selection,*,cutoff,alpaca_mapping_id=None,retained_alpaca_mapping_id=None,require_active=False):
    validate_selection(stores,selection,cutoff=cutoff,collection="news",require_active=require_active)
    market_rows=selected_market_rows(stores,selection,cutoff=cutoff,require_active=require_active)
    instruments=tuple(CurrentMarketNewsInstrument(r["instrument_id"],r["provider_symbol"],r["asset_type"]) for r in market_rows)
    equity={s.source_symbol:s.instrument_id for s in selection.eligible}
    retained={s.provider_symbol:s.instrument_id for s in selection.retained if s.asset_type=="etf"}
    symbols={};gaps=[(s.source_symbol,"fmp_identity: "+s.reason) for s in selection.gaps]
    def consume(rows,expected):
        rows_by={r["source_symbol"]:r for r in rows}
        if not set(expected)<=set(rows_by):
            raise ConflictError("News mapping does not account for the selected security scope")
        for source,instrument in expected.items():
            r=rows_by[source]
            if r["status"]!="resolved" or not r["instrument_id"] or not r["provider_symbol"]:
                gaps.append((source,r["reason"]));continue
            if r["instrument_id"]!=instrument:
                raise ConflictError("News provider mapping changes a selected security identity")
            native=r["provider_symbol"]
            if not isinstance(native,str) or not native or len(native)>32 or "," in native or any(x.isspace() for x in native):
                raise ValidationError("Alpaca news symbol is invalid")
            existing=symbols.get(native)
            if existing is not None and existing!=instrument:
                raise ConflictError("Alpaca news symbol identifies multiple instruments")
            symbols[native]=instrument
    with quiet_immutable_read_connection(stores,"market") as c:
        if alpaca_mapping_id is None:gaps.extend((s,"alpaca_mapping_not_available") for s in equity)
        else:
            meta,rows=_mapping(c,alpaca_mapping_id,cutoff)
            if meta["membership_snapshot_id"]!=selection.membership_snapshot_id:
                raise ConflictError("Alpaca news mapping belongs to another selected snapshot")
            consume(rows,equity)
        if retained_alpaca_mapping_id is None:gaps.extend((s,"retained_etf_alpaca_mapping_not_available") for s in retained)
        else:
            meta,rows=_mapping(c,retained_alpaca_mapping_id,cutoff)
            # The provider map must account for exactly the independently pinned ETFs.
            if {r["source_symbol"] for r in rows}!=set(retained):
                raise ConflictError("Retained ETF news mapping differs from its independent selection")
            consume(rows,retained)
    material={"selection":selection.scope_sha256,"alpaca_mapping":alpaca_mapping_id,
        "retained_alpaca_mapping":retained_alpaca_mapping_id,"symbols":sorted(symbols.items()),"gaps":sorted(gaps)}
    return SelectedNewsCoverage(instruments,_digest(material),selection.membership_snapshot_id,
        selection.mapping_id,alpaca_mapping_id,retained_alpaca_mapping_id,tuple(sorted(symbols)),tuple(sorted(gaps)))

def prepare_retained_etf_manifest(stores,selection,*,cutoff):
    """Derived local watchlist with original Stage10 snapshot IDs in the CSV."""
    validate_selection(stores,selection,cutoff=cutoff,collection="news")
    selected_market_rows(stores,selection,cutoff=cutoff)
    out=io.StringIO(newline="")
    writer=csv.writer(out,lineterminator="\n")
    writer.writerow(("Symbol","Description","SourceUniverseSnapshotID"))
    etfs=tuple(s for s in selection.retained if s.asset_type=="etf")
    for s in sorted(etfs,key=lambda s:s.provider_symbol):
        writer.writerow((s.provider_symbol,s.provider_symbol,s.universe_snapshot_id))
    return parse_manifest(body=out.getvalue().encode(),universe_id=RETAINED_ETF_UNIVERSE,
        name="Retained ETF news universe",source_reference="derived/retained-etf-news-"+_digest(
            sorted({s.universe_snapshot_id for s in etfs}))+".csv",captured_at=cutoff)

def read_host_news_coverage(stores,*,cutoff):
    """Choose the configured binding once before this invocation's network work."""
    from ..operations import collection_targets
    binding=load_bindings(collection_targets.HOST_BINDINGS_PATH)["news"]
    if binding.mode!="active":
        return None  # Existing caller retains its legacy roster-read timing.
    selection=pin_binding(stores,binding,cutoff=cutoff)
    with quiet_immutable_read_connection(stores,"market") as c:
        primary=c.execute("SELECT mapping_id FROM market_collection_mapping_snapshots WHERE membership_snapshot_id=? AND provider='alpaca' AND captured_at<=? ORDER BY version_sequence DESC LIMIT 1",
            (selection.membership_snapshot_id,_utc(cutoff))).fetchone()
        retained=c.execute("SELECT snapshot_id FROM market_collection_snapshots WHERE universe_id=? AND captured_at<=? ORDER BY version_sequence DESC LIMIT 1",
            (RETAINED_ETF_UNIVERSE,_utc(cutoff))).fetchone()
        secondary=None if retained is None else c.execute("SELECT mapping_id FROM market_collection_mapping_snapshots WHERE membership_snapshot_id=? AND provider='alpaca' AND captured_at<=? ORDER BY version_sequence DESC LIMIT 1",
            (retained[0],_utc(cutoff))).fetchone()
    if primary is None or secondary is None:
        raise StoreUnavailableError("Activated news requires both selected-equity and retained-ETF Alpaca mappings")
    return read_selected_news_coverage(stores,selection,cutoff=cutoff,alpaca_mapping_id=primary[0],
        retained_alpaca_mapping_id=secondary[0],require_active=True)
