"""Deterministic offline gate for the bounded Stage 10 FMP market profile.

The Stage 10 executable scope is a forward reconstruction.  This harness
proves its complete reviewed universe using injected synthetic FMP-shaped
bytes and fresh explicit roots; it never loads credentials or calls a provider.
"""

from __future__ import annotations

import hashlib
import http.client
import socket
import stat
import subprocess
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Mapping
from unittest import mock

from .errors import ConflictError, ValidationError
from .fingerprint import mutation_fingerprint
from .json_codec import dumps_strict
from .market.fmp_bulk_daily_prices import (
    CapturedFmpStage10Response,
    FMP_STAGE10_MAX_CONSTITUENT_BYTES,
    FMP_STAGE10_MAX_PRICE_BYTES,
    FMP_STAGE10_PRICE_PATH,
    FMP_STAGE10_TIMEOUT_SECONDS,
    FmpStage10Transport,
    capture_fmp_stage10_price,
    capture_fmp_stage10_universe,
    prepare_fmp_stage10_price_capture,
    prepare_fmp_stage10_universe_capture,
)
from .market.stage10_history_importer import Stage10HistoryImporter
from .market.stage10_scope import Stage10MarketScope, load_stage10_market_scope
from .migrations import initialize_all
from .operations.backup import backup_all, restore_all
from .operations.stage10_repopulation import (
    PrivateStage10CandidateState,
    build_stage10_repopulation_evidence,
    reconcile_stage10_market,
)
from .registry import CANONICAL_REGISTRY_PATH, load_registry, stage10_registry_profile
from .stage1 import explicit_store_map
from .stores import StoreMap, StoreRole, acquire_write_session, read_connection


_FIXED_MIGRATION_TIME = "2026-08-12T14:00:00Z"
_FIXED_UNIVERSE_TIME = datetime(2026, 8, 12, 14, 30, tzinfo=timezone.utc)
_FIXED_PRICE_TIME = datetime(2026, 8, 12, 15, 30, tzinfo=timezone.utc)
_FIXED_CORRECTION_TIME = datetime(2026, 8, 12, 16, 30, tzinfo=timezone.utc)
_FIXED_RECEIPT_TIME = datetime(2026, 8, 12, 17, 30, tzinfo=timezone.utc)
_OFFLINE_API_KEY = "offline-stage10-fmp-key"
_CODE_REVISION = hashlib.sha256(b"stage10-fmp-market-history-v1").hexdigest()
_ATTEMPT_ID = "stage10-offline-full-scope-v1"
_EXPECTED_UNIVERSE_COUNTS = {
    "sp500_current": 500,
    "nasdaq100_current": 100,
    "dow30_current": 30,
    "curated_etfs": 95,
    "major_indexes": 15,
}
_EXPECTED_ROSTER_COUNT = 738
_EXPECTED_MEMBER_COUNT = sum(_EXPECTED_UNIVERSE_COUNTS.values())


def _sha256_primitive(value: object) -> str:
    return hashlib.sha256(dumps_strict(value).encode("utf-8")).hexdigest()


def _prepare_clean_work_root(work_root: str | Path) -> Path:
    root = Path(work_root).expanduser().resolve(strict=False)
    if root == Path(root.anchor) or root == Path.home().resolve(strict=False):
        raise ConflictError("Stage 10 work root is too broad")
    if root.exists():
        if not root.is_dir():
            raise ConflictError("Stage 10 work root must be a directory")
        if next(root.iterdir(), None) is not None:
            raise ConflictError("Stage 10 clean rebuild requires an empty work root")
    else:
        root.mkdir(parents=True, exist_ok=False)
    return root


def _numbered_symbols(prefix: str, count: int) -> tuple[str, ...]:
    return tuple(f"{prefix}{number:04d}" for number in range(1, count + 1))


def _constituent_symbols(universe_id: str) -> tuple[str, ...]:
    if universe_id == "sp500_current":
        return ("AAPL", "MSFT", "NVDA", *_numbered_symbols("S", 497))
    if universe_id == "nasdaq100_current":
        return (
            "ALAB", "CRWV", "LITE", "NBIS", "RKLB", "SNDK", "SPCX", "TER", "WMT",
            *_numbered_symbols("N", 91),
        )
    if universe_id == "dow30_current":
        return ("AAPL", "MSFT", "JPM", *_numbered_symbols("D", 27))
    raise ValidationError("Stage 10 synthetic constituent universe is invalid")


def _universe_body(symbols: tuple[str, ...]) -> bytes:
    return dumps_strict(
        [{"symbol": symbol, "name": f"Offline synthetic {symbol} issuer"} for symbol in symbols]
    ).encode("utf-8")


def _price_start_date(
    *, symbol: str, asset_type: str, first_trade_date: str | None
) -> date:
    if first_trade_date is not None:
        return date.fromisoformat(first_trade_date)
    seed = int(hashlib.sha256(symbol.encode("ascii")).hexdigest()[:8], 16)
    if asset_type == "index":
        return date(1950, 1, 1) + timedelta(days=seed % 20_000)
    return date(1980, 1, 1) + timedelta(days=seed % 15_000)


def _price_body(
    *, symbol: str, start_date: date, asset_type: str, correction: bool = False
) -> bytes:
    seed = int(hashlib.sha256(symbol.encode("ascii")).hexdigest()[:8], 16)
    base = 25 + seed % 900
    rows: list[dict[str, object]] = []
    for ordinal, trade_date in enumerate((start_date, start_date + timedelta(days=1))):
        open_value = base + ordinal
        close_value = open_value + 1 + (1 if correction and ordinal == 0 else 0)
        rows.append(
            {
                "symbol": symbol,
                "date": trade_date.isoformat(),
                "open": open_value,
                "high": close_value + 1,
                "low": open_value - 1,
                "close": close_value,
                "volume": 0 if asset_type == "index" else 1_000_000 + seed % 100_000,
                "change": 1,
                "changePercent": 1,
                "vwap": open_value,
            }
        )
    return dumps_strict(rows).encode("utf-8")


class _OfflineTransport:
    """Injected FMP transport serving only prebuilt fixture bytes."""

    def __init__(
        self, *, universe_bodies: Mapping[str, bytes], price_bodies: Mapping[str, bytes]
    ) -> None:
        self._universe_bodies = dict(universe_bodies)
        self._price_bodies = dict(price_bodies)
        self.universe_calls = 0
        self.price_calls = 0

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> CapturedFmpStage10Response:
        if dict(headers) != {"apikey": _OFFLINE_API_KEY}:
            raise ValidationError("Stage 10 offline transport header drifted")
        if timeout_seconds != FMP_STAGE10_TIMEOUT_SECONDS:
            raise ValidationError("Stage 10 offline transport timeout drifted")
        if path in self._universe_bodies:
            if dict(query) or max_bytes != FMP_STAGE10_MAX_CONSTITUENT_BYTES:
                raise ValidationError("Stage 10 offline universe request drifted")
            self.universe_calls += 1
            return CapturedFmpStage10Response(200, "application/json", self._universe_bodies[path])
        if path == FMP_STAGE10_PRICE_PATH:
            if set(query) != {"symbol"} or max_bytes != FMP_STAGE10_MAX_PRICE_BYTES:
                raise ValidationError("Stage 10 offline price request drifted")
            try:
                body = self._price_bodies[query["symbol"]]
            except KeyError as exc:
                raise ValidationError("Stage 10 offline price symbol drifted") from exc
            self.price_calls += 1
            return CapturedFmpStage10Response(200, "application/json", body)
        raise ValidationError("Stage 10 offline request path drifted")



@contextmanager
def _offline_guard() -> Iterator[list[str]]:
    """Fail the fixture gate if a socket or subprocess is reached."""

    calls: list[str] = []

    def blocked(name: str):
        def fail(*args: object, **kwargs: object) -> None:
            del args, kwargs
            calls.append(name)
            raise AssertionError(f"Stage 10 offline guard blocked {name}")

        return fail

    with (
        mock.patch.object(socket, "create_connection", blocked("socket.create_connection")),
        mock.patch.object(socket.socket, "connect", blocked("socket.socket.connect")),
        mock.patch.object(http.client, "HTTPSConnection", blocked("HTTPSConnection")),
        mock.patch.object(subprocess, "Popen", blocked("subprocess.Popen")),
        mock.patch.object(subprocess, "run", blocked("subprocess.run")),
        mock.patch.object(subprocess, "call", blocked("subprocess.call")),
        mock.patch.object(subprocess, "check_call", blocked("subprocess.check_call")),
        mock.patch.object(subprocess, "check_output", blocked("subprocess.check_output")),
    ):
        yield calls


def _capture_universes(
    scope: Stage10MarketScope,
    transport: FmpStage10Transport,
) -> dict[str, object]:
    captures: dict[str, object] = {}
    for source in scope.universe_sources:
        symbols = _constituent_symbols(source.id)
        if len(symbols) != _EXPECTED_UNIVERSE_COUNTS[source.id]:
            raise ValidationError("Stage 10 synthetic universe count drifted")
        captures[source.id] = capture_fmp_stage10_universe(
            prepare_fmp_stage10_universe_capture(source.endpoint_path),
            api_key=_OFFLINE_API_KEY,
            transport=transport,
        )
    return captures


def _published_roster(store_map: StoreMap) -> tuple[tuple[str, str, str | None], ...]:
    with read_connection(store_map, StoreRole.MARKET) as connection:
        rows = tuple(
            (
                str(row["provider_symbol"]),
                str(row["asset_type"]),
                None if row["first_trade_date"] is None else str(row["first_trade_date"]),
            )
            for row in connection.execute(
                """
                SELECT provider_symbol, asset_type, first_trade_date
                FROM stage10_instruments
                WHERE provider='fmp'
                ORDER BY provider_symbol
                """
            )
        )
    if len(rows) != _EXPECTED_ROSTER_COUNT or len({row[0] for row in rows}) != len(rows):
        raise ValidationError("Stage 10 synthetic full roster is incomplete")
    if any(symbol in {"^RUT", "IWM"} or "russell" in symbol.casefold() for symbol, _, _ in rows):
        raise ValidationError("Stage 10 synthetic roster contains Russell")
    counts = {
        asset_type: sum(row[1] == asset_type for row in rows)
        for asset_type in ("equity", "etf", "index")
    }
    if counts != {"equity": 628, "etf": 95, "index": 15}:
        raise ValidationError("Stage 10 synthetic roster asset types drifted")
    return rows


def _capture_price_candidates(
    importer: Stage10HistoryImporter,
    roster: tuple[tuple[str, str, str | None], ...],
    transport: FmpStage10Transport,
) -> tuple[tuple[str, str, date, object], ...]:
    candidates: list[tuple[str, str, date, object]] = []
    for symbol, asset_type, first_trade_date in roster:
        start = _price_start_date(
            symbol=symbol,
            asset_type=asset_type,
            first_trade_date=first_trade_date,
        )
        capture = capture_fmp_stage10_price(
            prepare_fmp_stage10_price_capture(symbol),
            api_key=_OFFLINE_API_KEY,
            transport=transport,
        )
        candidates.append((symbol, asset_type, start, importer.prepare_price_capture(capture)))
    if len(candidates) != _EXPECTED_ROSTER_COUNT:
        raise ValidationError("Stage 10 did not prepare every reviewed symbol")
    return tuple(candidates)


def _publish_many(
    importer: Stage10HistoryImporter,
    candidates: tuple[object, ...],
    store_map: StoreMap,
) -> tuple[object, ...]:
    receipts: list[object] = []
    with acquire_write_session(store_map, (StoreRole.MARKET,)) as held_locks:
        for candidate in candidates:
            receipts.append(importer.publish_prepared(candidate, held_locks=held_locks))
    return tuple(receipts)


def _assert_succeeded(receipts: tuple[object, ...], expected: int, label: str) -> int:
    if len(receipts) != expected:
        raise ValidationError(f"Stage 10 {label} receipt count drifted")
    written = 0
    for receipt in receipts:
        if getattr(receipt, "outcome", None) != "succeeded":
            raise ValidationError(f"Stage 10 {label} publication did not succeed")
        count = getattr(receipt, "written_count", None)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValidationError(f"Stage 10 {label} publication count is invalid")
        written += count
    return written


def _assert_unchanged(receipts: tuple[object, ...], expected: int) -> None:
    if len(receipts) != expected or any(
        getattr(receipt, "outcome", None) != "unchanged"
        or getattr(receipt, "written_count", None) != 0
        for receipt in receipts
    ):
        raise ValidationError("Stage 10 semantic replay was not a total no-write")


def _stage10_counts(store_map: StoreMap) -> dict[str, int]:
    relations = (
        "stage10_instruments",
        "stage10_universe_captures",
        "stage10_scope_snapshots",
        "stage10_universes",
        "stage10_universe_snapshots",
        "stage10_universe_snapshot_members",
        "stage10_daily_price_captures",
        "stage10_daily_price_versions",
        "stage10_daily_prices",
    )
    with read_connection(store_map, StoreRole.MARKET) as connection:
        result = {
            relation: int(connection.execute(f'SELECT count(*) FROM "{relation}"').fetchone()[0])
            for relation in relations
        }
    expected = {
        "stage10_instruments": _EXPECTED_ROSTER_COUNT,
        "stage10_universe_captures": 3,
        "stage10_scope_snapshots": 1,
        "stage10_universes": 5,
        "stage10_universe_snapshots": 5,
        "stage10_universe_snapshot_members": _EXPECTED_MEMBER_COUNT,
        "stage10_daily_price_captures": _EXPECTED_ROSTER_COUNT + 1,
        "stage10_daily_price_versions": _EXPECTED_ROSTER_COUNT * 2 + 1,
        "stage10_daily_prices": _EXPECTED_ROSTER_COUNT * 2,
    }
    if result != expected:
        raise ValidationError("Stage 10 full-scope canonical counts drifted")
    return result


def _correction_rehearsal(
    store_map: StoreMap,
    registry: object,
    scope: Stage10MarketScope,
    *,
    symbol: str,
    asset_type: str,
    start_date: date,
) -> dict[str, object]:
    transport = _OfflineTransport(
        universe_bodies={},
        price_bodies={
            symbol: _price_body(
                symbol=symbol,
                start_date=start_date,
                asset_type=asset_type,
                correction=True,
            )
        },
    )
    importer = Stage10HistoryImporter(
        store_map,
        registry,  # type: ignore[arg-type]
        scope,
        clock=lambda: _FIXED_CORRECTION_TIME,
    )
    capture = capture_fmp_stage10_price(
        prepare_fmp_stage10_price_capture(symbol),
        api_key=_OFFLINE_API_KEY,
        transport=transport,
    )
    receipt = importer.publish_prepared(importer.prepare_price_capture(capture))
    if receipt.outcome != "succeeded" or receipt.written_count != 2 or transport.price_calls != 1:
        raise ValidationError("Stage 10 correction rehearsal did not publish exactly once")
    with read_connection(store_map, StoreRole.MARKET) as connection:
        row = connection.execute(
            """
            SELECT version.close_value
            FROM stage10_daily_prices AS current
            JOIN stage10_daily_price_versions AS version
              ON version.version_id=current.current_version_id
            JOIN stage10_instruments AS instrument
              ON instrument.instrument_id=current.instrument_id
            WHERE instrument.provider_symbol=? AND current.trade_date=?
            """,
            (symbol, start_date.isoformat()),
        ).fetchone()
        maximum = int(
            connection.execute(
                "SELECT max(correction_sequence) FROM stage10_daily_price_versions"
            ).fetchone()[0]
        )
    if row is None or maximum != 2:
        raise ValidationError("Stage 10 correction lineage is invalid")
    return {
        "outcome": receipt.outcome,
        "symbol": symbol,
        "trade_date": start_date.isoformat(),
        "capture_requests": transport.price_calls,
        "max_correction_sequence": maximum,
        "corrected_close_sha256": hashlib.sha256(
            str(row["close_value"]).encode("ascii")
        ).hexdigest(),
    }


def _receipt_summary(root: Path, receipt: object) -> dict[str, object]:
    directory = root / "promotion-candidates"
    files = tuple(sorted(directory.glob("*.json")))
    if len(files) != 1:
        raise ValidationError("Stage 10 did not publish exactly one candidate receipt")
    if stat.S_IMODE(directory.stat().st_mode) != 0o700:
        raise ValidationError("Stage 10 candidate receipt directory is not private")
    if stat.S_IMODE(files[0].stat().st_mode) != 0o600:
        raise ValidationError("Stage 10 candidate receipt is not private")
    payload = files[0].read_bytes()
    expected = (dumps_strict(receipt.to_primitive()) + "\n").encode("utf-8")
    if payload != expected:
        raise ValidationError("Stage 10 candidate receipt bytes do not match its result")
    return {
        "count": 1,
        "receipt_sha256": receipt.receipt_sha256,
        "file_sha256": hashlib.sha256(payload).hexdigest(),
        "directory_mode": "0700",
        "file_mode": "0600",
    }


def run_clean_stage10_rebuild(
    *,
    project_root: str | Path,
    work_root: str | Path,
) -> dict[str, object]:
    """Rebuild the complete bounded Stage 10 profile without live activity."""

    project = Path(project_root).expanduser().resolve(strict=True)
    root = _prepare_clean_work_root(work_root)
    registry = stage10_registry_profile(
        load_registry(
            project / CANONICAL_REGISTRY_PATH,
            project_root=project,
            environment={},
        )
    )
    scope = load_stage10_market_scope(project / "config" / "stage10_market_scope.json")
    if registry.schema_version != "1.6.0" or registry.registry_version != "2.8.0":
        raise ValidationError("Stage 10 requires the reviewed 1.6.0/2.8.0 registry")
    source_root = root / "source"
    source_root.mkdir(mode=0o700)
    source = explicit_store_map(source_root)
    migration_heads = initialize_all(source, registry, applied_at=_FIXED_MIGRATION_TIME)
    if migration_heads["market"][-1] != "market:0010_stage10_market_history":
        raise ValidationError("Stage 10 market migration head is invalid")

    universe_bodies = {
        source.endpoint_path: _universe_body(_constituent_symbols(source.id))
        for source in scope.universe_sources
    }
    price_bodies: dict[str, bytes] = {}
    with _offline_guard() as blocked_calls:
        universe_transport = _OfflineTransport(
            universe_bodies=universe_bodies,
            price_bodies=price_bodies,
        )
        universe_importer = Stage10HistoryImporter(
            source, registry, scope, clock=lambda: _FIXED_UNIVERSE_TIME
        )
        captures = _capture_universes(scope, universe_transport)
        before_universe_publish = mutation_fingerprint(source)
        universe_candidates = universe_importer.prepare_universe_publications(
            captures  # type: ignore[arg-type]
        )
        if mutation_fingerprint(source) != before_universe_publish:
            raise ValidationError("Stage 10 universe preparation mutated a store")
        universe_publication = _publish_many(
            universe_importer, tuple(universe_candidates), source
        )
        universe_written = _assert_succeeded(universe_publication, 5, "universe")
        roster = _published_roster(source)
        for symbol, asset_type, first_trade_date in roster:
            price_bodies[symbol] = _price_body(
                symbol=symbol,
                start_date=_price_start_date(
                    symbol=symbol,
                    asset_type=asset_type,
                    first_trade_date=first_trade_date,
                ),
                asset_type=asset_type,
            )
        price_transport = _OfflineTransport(
            universe_bodies={},
            price_bodies=price_bodies,
        )
        price_importer = Stage10HistoryImporter(
            source, registry, scope, clock=lambda: _FIXED_PRICE_TIME
        )
        before_price_prepare = mutation_fingerprint(source)
        price_candidates = _capture_price_candidates(
            price_importer,
            roster,
            price_transport,
        )
        if mutation_fingerprint(source) != before_price_prepare:
            raise ValidationError("Stage 10 price preparation mutated a store")
        if (
            universe_transport.universe_calls != 3
            or price_transport.price_calls != _EXPECTED_ROSTER_COUNT
        ):
            raise ValidationError("Stage 10 offline request count drifted")
        price_publication = _publish_many(
            price_importer,
            tuple(candidate for _, _, _, candidate in price_candidates),
            source,
        )
        price_written = _assert_succeeded(
            price_publication, _EXPECTED_ROSTER_COUNT, "price"
        )
        before_replay = mutation_fingerprint(source)
        _assert_unchanged(
            _publish_many(universe_importer, tuple(universe_candidates), source), 5
        )
        _assert_unchanged(
            _publish_many(
                price_importer,
                tuple(candidate for _, _, _, candidate in price_candidates),
                source,
            ),
            _EXPECTED_ROSTER_COUNT,
        )
        if mutation_fingerprint(source) != before_replay:
            raise ValidationError("Stage 10 exact replay changed a store")
        correction_symbol, correction_asset_type, correction_start, _ = next(
            item for item in price_candidates if item[0] == "AAPL"
        )
        correction = _correction_rehearsal(
            source,
            registry,
            scope,
            symbol=correction_symbol,
            asset_type=correction_asset_type,
            start_date=correction_start,
        )
        counts = _stage10_counts(source)
        reconciliation = reconcile_stage10_market(source, registry, scope)
        backup = backup_all(source, registry, target_root=root / "backup")
        restored = restore_all(backup, registry, target_root=root / "restored")
        evidence = build_stage10_repopulation_evidence(
            source, restored.store_map, registry, scope, replay_unchanged=True
        )
        receipt_root = root / "private-state"
        receipt = PrivateStage10CandidateState(receipt_root).publish(
            evidence,
            code_revision=_CODE_REVISION,
            now=_FIXED_RECEIPT_TIME,
            attempt_id=_ATTEMPT_ID,
        )
        receipt_summary = _receipt_summary(receipt_root, receipt)
        if blocked_calls:
            raise ValidationError("Stage 10 offline gate attempted a live provider call")

    result: dict[str, object] = {
        "contract": "quant_data.stage10_rebuild_evidence",
        "contract_version": "1.0.0",
        "registry_revision": registry.revision,
        "registry_schema_version": registry.schema_version,
        "registry_source_sha256": registry.source_sha256,
        "scope_manifest_sha256": scope.manifest_sha256,
        "target_profile_id": scope.target_profile_id,
        "provider": scope.provider,
        "universe_counts": _EXPECTED_UNIVERSE_COUNTS,
        "deduplicated_roster_count": len(roster),
        "roster_asset_type_counts": {"equity": 628, "etf": 95, "index": 15},
        "daily_bars_per_instrument": 2,
        "variable_earliest_date_count": len(
            {
                _price_start_date(
                    symbol=symbol, asset_type=asset_type, first_trade_date=first_trade_date
                ).isoformat()
                for symbol, asset_type, first_trade_date in roster
            }
        ),
        "synthetic_transport": {
            "universe_request_count": 3,
            "price_request_count": _EXPECTED_ROSTER_COUNT + 1,
            "response_kind": "synthetic_fmp_shaped_offline_only",
        },
        "universe_publication_written_count": universe_written,
        "price_publication_written_count": price_written,
        "semantic_replay_outcome": "unchanged",
        "correction_rehearsal": correction,
        "canonical_counts": counts,
        "reconciliation_sha256": reconciliation.sha256,
        "repopulation_evidence_sha256": evidence.sha256,
        "candidate_receipt": receipt_summary,
        "migration_heads_sha256": _sha256_primitive(migration_heads),
        "market_migration_head": migration_heads["market"][-1],
        "backup_source_sha256": backup.source_logical_manifest_sha256,
        "backup_copy_sha256": backup.backup_logical_manifest_sha256,
        "restored_sha256": restored.restored_logical_manifest_sha256,
        "backup_restore_equal": True,
        "operational_promotion_performed": False,
        "old_store_retirement_performed": False,
        "live_provider_calls": 0,
        "credential_values_recorded": 0,
        "public_export_ids": [],
    }
    serialized = dumps_strict(result)
    for path in (project, root, source_root):
        if str(path) in serialized:
            raise ValidationError("Stage 10 evidence exposed a physical path")
    if _OFFLINE_API_KEY in serialized:
        raise ValidationError("Stage 10 evidence exposed a credential value")
    result["sha256"] = _sha256_primitive(result)
    return result


def compare_clean_stage10_rebuilds(
    *,
    project_root: str | Path,
    first_work_root: str | Path,
    second_work_root: str | Path,
) -> dict[str, object]:
    """Require two clean Stage 10 rebuilds to emit identical path-free evidence."""

    first = run_clean_stage10_rebuild(
        project_root=project_root,
        work_root=first_work_root,
    )
    second = run_clean_stage10_rebuild(
        project_root=project_root,
        work_root=second_work_root,
    )
    if dumps_strict(first) != dumps_strict(second):
        raise ValidationError("Two clean Stage 10 rebuilds produced different evidence")
    return first


__all__ = ("compare_clean_stage10_rebuilds", "run_clean_stage10_rebuild")
