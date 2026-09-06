"""One-time, retained-response publisher for the Sep. 6 FMP price-gap repair.

This module has no transport, credential, scheduler, retry, or caller-selected
store path.  A host acquisition helper retains the three authorized responses;
this adapter publishes one retained complete response through the fixed
canonical Stage 12B path.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final, Mapping

from ..json_codec import dumps_strict
from ..market.stage12_incremental import (
    PreparedStage12BPublication,
    Stage12BFixtureRequest,
    Stage12BFixtureResponse,
    Stage12BIncrementalCollector,
    Stage12BPublicationReceipt,
)
from ..market.stage12_scope import load_stage12_market_v1_scope
from ..market.stage12b_scope import load_stage12b_incremental_market_v1_scope

_PROJECT_ROOT: Final = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
_STAGE12A_SCOPE: Final = _PROJECT_ROOT / "config" / "stage12_market_v1_scope.json"
_STAGE12B_SCOPE: Final = _PROJECT_ROOT / "config" / "stage12b_incremental_market_v1_scope.json"
RESEARCH_PRICE_GAP_REPAIR_ENDPOINT: Final = "/stable/historical-price-eod/full"
RESEARCH_PRICE_GAP_REPAIR_SESSIONS: Final = (
    "2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20",
    "2026-08-21", "2026-08-24", "2026-08-25", "2026-08-26",
    "2026-08-27", "2026-08-28", "2026-08-31", "2026-09-01",
)
RESEARCH_PRICE_GAP_REPAIR_PLAN: Final = {
    "AAPL": {
        "asset_type": "equity", "from": "2026-08-17", "to": "2026-08-28",
        "session_dates": RESEARCH_PRICE_GAP_REPAIR_SESSIONS[:10],
    },
    "MSFT": {
        "asset_type": "equity", "from": "2026-08-17", "to": "2026-09-01",
        "session_dates": RESEARCH_PRICE_GAP_REPAIR_SESSIONS,
    },
    "SPY": {
        "asset_type": "etf", "from": "2026-08-17", "to": "2026-09-01",
        "session_dates": RESEARCH_PRICE_GAP_REPAIR_SESSIONS,
    },
}
RESEARCH_PRICE_GAP_REPAIR_AUTHORITY: Final = {
    "collector": "fmp.market.research_gap_repair",
    "date": "2026-09-06",
    "endpoint_path": RESEARCH_PRICE_GAP_REPAIR_ENDPOINT,
    "max_bytes": 3 * 1024 * 1024,
    "max_requests": 3,
    "max_rows": 34,
    "max_seconds": 90,
    "request_policy": "three_retained_single_attempt_fmp_historical_price_eod_full",
    "symbols": ["AAPL", "MSFT", "SPY"],
    "version": "1.0.0",
}


def _sha256_json(value: object) -> str:
    return hashlib.sha256(dumps_strict(value).encode("utf-8")).hexdigest()


RESEARCH_PRICE_GAP_REPAIR_AUTHORITY_SHA256: Final = _sha256_json(
    RESEARCH_PRICE_GAP_REPAIR_AUTHORITY
)
RESEARCH_PRICE_GAP_REPAIR_PLAN_SHA256: Final = _sha256_json(
    RESEARCH_PRICE_GAP_REPAIR_PLAN
)


def _repair_instruments() -> dict[str, tuple[str, str, str, tuple[str, ...]]]:
    return {
        symbol: (
            str(item["asset_type"]),
            str(item["from"]),
            str(item["to"]),
            tuple(item["session_dates"]),
        )
        for symbol, item in RESEARCH_PRICE_GAP_REPAIR_PLAN.items()
    }


def _canonical_collector() -> Stage12BIncrementalCollector:
    return Stage12BIncrementalCollector._for_canonical_research_repair(
        scope=load_stage12b_incremental_market_v1_scope(_STAGE12B_SCOPE),
        stage12a_scope=load_stage12_market_v1_scope(_STAGE12A_SCOPE),
        research_repair_authority_sha256=RESEARCH_PRICE_GAP_REPAIR_AUTHORITY_SHA256,
        research_repair_plan_sha256=RESEARCH_PRICE_GAP_REPAIR_PLAN_SHA256,
        research_repair_instruments=_repair_instruments(),
    )


def _fixture_repair_collector(
    *,
    fixture_root: Path,
    market_store: Path,
    scope: object,
    stage12a_scope: object,
    stage12a_scope_source: Path,
) -> Stage12BIncrementalCollector:
    """Private explicit-temp constructor used only by offline operation tests."""

    return Stage12BIncrementalCollector(
        fixture_root=fixture_root,
        market_store=market_store,
        scope=scope,  # type: ignore[arg-type]
        stage12a_scope=stage12a_scope,  # type: ignore[arg-type]
        stage12a_scope_source=stage12a_scope_source,
        _research_repair=True,
        _research_repair_authority_sha256=RESEARCH_PRICE_GAP_REPAIR_AUTHORITY_SHA256,
        _research_repair_plan_sha256=RESEARCH_PRICE_GAP_REPAIR_PLAN_SHA256,
        _research_repair_instruments=_repair_instruments(),
    )


def _prepare_research_price_response(
    collector: Stage12BIncrementalCollector,
    *,
    symbol: str,
    body: bytes,
    status: int,
    media_type: str,
    captured_at: str,
) -> PreparedStage12BPublication:
    item = RESEARCH_PRICE_GAP_REPAIR_PLAN.get(symbol)
    if item is None:
        raise ValueError("Symbol is outside the authorized research repair plan")
    return collector.prepare(
        Stage12BFixtureRequest(
            symbol=symbol,
            from_date=str(item["from"]),
            to_date=str(item["to"]),
            session_dates=tuple(item["session_dates"]),
            captured_at=captured_at,
        ),
        Stage12BFixtureResponse(
            status=status,
            media_type=media_type,
            body=body,
            elapsed_seconds=0,
        ),
    )


def publish_research_price_response(
    *, symbol: str, body: bytes, status: int, media_type: str, captured_at: str
) -> Stage12BPublicationReceipt:
    """Publish one retained authorized response to the fixed canonical store."""

    collector = _canonical_collector()
    return collector.publish(
        _prepare_research_price_response(
            collector,
            symbol=symbol,
            body=body,
            status=status,
            media_type=media_type,
            captured_at=captured_at,
        )
    )


__all__ = (
    "RESEARCH_PRICE_GAP_REPAIR_AUTHORITY",
    "RESEARCH_PRICE_GAP_REPAIR_AUTHORITY_SHA256",
    "RESEARCH_PRICE_GAP_REPAIR_ENDPOINT",
    "RESEARCH_PRICE_GAP_REPAIR_PLAN",
    "RESEARCH_PRICE_GAP_REPAIR_PLAN_SHA256",
    "RESEARCH_PRICE_GAP_REPAIR_SESSIONS",
    "publish_research_price_response",
)
