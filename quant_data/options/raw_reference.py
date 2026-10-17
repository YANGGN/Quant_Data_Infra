"""Retained FMP as-traded closes for the finite cross-asset reference repair.

The adjClose field on this specific endpoint is *not* the adjusted close from
the full-EOD endpoint. The endpoint binding, original row and receipt travel
with every reference. No split factors or inferred prices are applied.
"""
from datetime import date, datetime
import hashlib, json, re

from .model import canonical_json, decimal, integer, NY
from .universe import CROSS_ASSET_ETFS

CONTRACT = "fmp_non_split_adjusted_close_v1"
ENDPOINT = "/stable/historical-price-eod/non-split-adjusted"
DOCUMENTATION = "https://site.financialmodelingprep.com/developer/docs/stable/historical-price-eod-non-split-adjusted"


def _receipt(receipt, symbol, session):
    params = receipt.get("params", {})
    if (receipt.get("endpoint") != ENDPOINT or receipt.get("status") != 200
            or receipt.get("attempts") != 1 or set(params) != {"symbol", "from", "to"}
            or params["symbol"] != symbol or symbol not in CROSS_ASSET_ETFS
            or type(receipt.get("response_bytes")) is not int
            or not 0 < receipt["response_bytes"] <= 2*1024**2
            or re.fullmatch(r"[a-f0-9]{64}", receipt.get("response_sha256", "")) is None):
        raise ValueError("raw_reference_receipt")
    start, end, day = (date.fromisoformat(x) for x in (params["from"], params["to"], session))
    captured = datetime.fromisoformat(receipt["captured_at"])
    if (not 0 <= (end-start).days < 366 or not start <= day <= end
            or captured.tzinfo is None or captured.astimezone(NY).date() <= day):
        raise ValueError("raw_reference_date")


def _row(row, symbol, session):
    if (set(row) != {"symbol", "date", "adjOpen", "adjHigh", "adjLow", "adjClose", "volume"}
            or row["symbol"] != symbol or row["date"] != session
            or date.fromisoformat(session).isoformat() != session):
        raise ValueError("raw_reference_row_identity")
    op, high, low, close = [decimal(row[k]) for k in ("adjOpen", "adjHigh", "adjLow", "adjClose")]
    if (any(v is None or v <= 0 for v in (op, high, low, close))
            or not low <= op <= high or not low <= close <= high
            or integer(row["volume"]) is None or integer(row["volume"]) <= 0):
        raise ValueError("raw_reference_ohlc")
    return close


def _metadata(symbol):
    return {"provider": "fmp", "provider_symbol": symbol, "adjustment_status": "unadjusted",
        "source_price_field": "adjClose", "source_adjustment_binding": CONTRACT,
        "endpoint": ENDPOINT, "source_adjustment_documentation": DOCUMENTATION,
        "source_publication_time": None, "availability_basis": "local_capture"}


def prepare_raw_references(body, receipt, *, artifact):
    """Verify the full retained HTTP body before extracting dated references."""
    if (not isinstance(body, bytes) or len(body) != receipt.get("response_bytes")
            or hashlib.sha256(body).hexdigest() != receipt.get("response_sha256")):
        raise ValueError("raw_reference_body_hash")
    rows = json.loads(body)
    if not isinstance(rows, list) or not 1 <= len(rows) <= 366:
        raise ValueError("raw_reference_row_count")
    symbol = receipt["params"]["symbol"]
    result = {}
    for row in rows:
        session = row["date"]
        _receipt(receipt, symbol, session)
        value = _row(row, symbol, session)
        if session in result:
            raise ValueError("raw_reference_duplicate_date")
        reference = {"contract": CONTRACT, "value": str(value), "metadata": _metadata(symbol),
            "observation": {"period_start": session, "period_end": session, "precision": "date",
                            "captured_at": receipt["captured_at"]},
            "row": row, "receipt": receipt, "artifact": artifact,
            "artifact_sha256": receipt["response_sha256"],
            "row_sha256": hashlib.sha256(canonical_json(row).encode()).hexdigest()}
        validate_raw_reference(reference, symbol=symbol, session=session)
        result[session] = reference
    return result


def validate_raw_reference(reference, *, symbol, session):
    """Fail closed on relabelled adjusted data, wrong identities or stale dates."""
    receipt, row = reference.get("receipt", {}), reference.get("row", {})
    _receipt(receipt, symbol, session)
    value = _row(row, symbol, session)
    if (reference.get("contract") != CONTRACT or reference.get("metadata") != _metadata(symbol)
            or decimal(reference.get("value")) != value
            or reference.get("observation") != {"period_start": session, "period_end": session,
                "precision": "date", "captured_at": receipt["captured_at"]}
            or reference.get("artifact_sha256") != receipt["response_sha256"]
            or reference.get("row_sha256") != hashlib.sha256(canonical_json(row).encode()).hexdigest()):
        raise ValueError("raw_reference_binding")
    return {"status": "provider_documented_as_traded_close", "contract": CONTRACT,
            "precision": "date", "parity_check": "not_required_for_direct_unadjusted_source",
            "deliverable_basis": "provider_root_only_unverified"}
