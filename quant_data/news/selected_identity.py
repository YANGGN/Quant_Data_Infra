"""Prepare news-only Alpaca associations from original asset catalogue evidence.

Exact provider symbols are resolved against already pinned FMP instruments.
No issuer substitution, inferred share-class alias, or network request occurs.
"""
from collections.abc import Mapping

from ..errors import ValidationError, ResourceLimitError
from ..json_codec import dumps_strict, loads_strict
from ..market.collection_mappings import IdentityEvidence, prepare_mapping, unresolved_mapping
from ..market.collection_universe import MAX_BYTES, MAX_MEMBERS, _utc


def prepare_alpaca_news_mapping(*, membership_snapshot_id, instruments, evidence,
                                captured_at, source_reference):
    """Account for every pinned source symbol, including explicit catalogue gaps."""
    if (not isinstance(instruments, Mapping) or not 1 <= len(instruments) <= MAX_MEMBERS
            or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v
                   for k, v in instruments.items())):
        raise ValidationError("News mapping requires a bounded pinned instrument map")
    if not isinstance(evidence, IdentityEvidence) or evidence.provider != "alpaca":
        raise ValidationError("News mapping requires original Alpaca asset evidence")
    if _utc(evidence.captured_at) > _utc(captured_at):
        raise ValidationError("News mapping cannot see future asset evidence")
    assets = loads_strict(evidence.body, max_bytes=MAX_BYTES)
    if not isinstance(assets, list) or len(assets) > 100000:
        raise ResourceLimitError("Alpaca asset catalogue exceeds its row bound")
    candidates = {}
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            raise ValidationError("Alpaca asset catalogue contains a non-object")
        symbol = asset.get("symbol")
        if not isinstance(symbol, str) or symbol not in instruments:
            continue
        if (not isinstance(asset.get("id"), str) or not asset["id"]
                or asset.get("class") != "us_equity"
                or asset.get("status") not in ("active", "inactive")):
            raise ValidationError("Selected Alpaca asset identity is invalid")
        candidates.setdefault(symbol, []).append((index, asset))
    rows = []
    for symbol, instrument in sorted(instruments.items()):
        row = unresolved_mapping(symbol, "No exact symbol in the retained Alpaca asset catalogue")
        matches = candidates.get(symbol, [])
        if len({(asset["id"], asset["status"]) for _, asset in matches}) > 1:
            row.update(status="ambiguous", reason="Competing Alpaca identities or statuses for the same symbol")
        elif matches:
            index, asset = matches[0]
            if asset["status"] != "active":
                row.update(status="unsupported", reason="Alpaca asset is inactive; current news support is unverified")
            else:
                row.update(status="resolved", provider_symbol=symbol, provider_subject=asset["id"],
                           instrument_id=instrument, evidence_sha256=evidence.sha256,
                           evidence_reference=evidence.source_reference, evidence_pointer=f"/{index}",
                           symbol_field="symbol", subject_field="id",
                           reason="Exact active Alpaca asset symbol linked to the pinned same-member FMP instrument")
        rows.append(row)
    used = any(row["status"] == "resolved" for row in rows)
    return prepare_mapping(membership_snapshot_id=membership_snapshot_id, provider="alpaca",
                           captured_at=captured_at, source_reference=source_reference,
                           body=dumps_strict(rows).encode(), evidence=(evidence,) if used else ())
