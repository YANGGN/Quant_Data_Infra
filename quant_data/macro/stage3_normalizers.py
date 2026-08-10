"""Strict, offline normalizers for the bounded Stage 3 macro fixture lane.

The functions in this module deliberately operate on already-fetched provider
shapes.  They neither open a database nor perform network I/O.  Their sole
job is to reject incomplete acquisitions and produce deterministic semantic
identities before :class:`~quant_data.ingestion.IngestionCoordinator` opens a
write transaction.

These are prospective reconstruction contracts.  They encode the recovered
source-specific no-write exceptions without claiming to reproduce erased
provider adapters or historical migration bytes.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Sequence

from ..errors import Issue, ValidationError
from ..json_codec import dumps_strict, loads_strict
from ..temporal import TemporalPrecision, TemporalValue


_FIXTURE_SCHEMA_VERSION = "stage3_macro_fixture_v1"
_FAMILIES = frozenset(
    {
        "gdp",
        "treasury",
        "economic_calendar",
        "soma",
        "eia_retail",
        "eia_weekly",
        "recession",
        "bls",
        "bis",
        "chicagofed",
        "bea",
    }
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CUSIP_VALUE = re.compile(r"^[0-9A-Z]{9}$")
_FAILED_CAPTURE_STATES = frozenset(
    {
        "partial",
        "error",
        "timeout",
        "rate_limit",
        "http_429",
        "failed",
        "rejected",
    }
)
_SECURITY_SHAPED_TOKENS = (
    "cusip",
    "security",
    "holding",
    "position",
    "isin",
    "sedol",
)
_GENERIC_PAYLOAD_KEYS: dict[str, frozenset[str]] = {
    "gdp": frozenset({"series", "releases", "observations", "gdp_vintages"}),
    "treasury": frozenset({"series", "releases", "observations", "curves"}),
    "economic_calendar": frozenset(
        {"series", "releases", "observations", "calendar_events"}
    ),
    "eia_retail": frozenset(
        {"series", "releases", "observations", "retail_snapshot"}
    ),
    "recession": frozenset(
        {"series", "releases", "observations", "recession_periods"}
    ),
}


@dataclass(frozen=True, slots=True)
class Stage3MacroFixtureCandidate:
    """A normalized, complete fixture candidate safe to pass to a writer."""

    fixture_id: str
    family: str
    provider: str
    captured_at: TemporalValue
    request_scope: Mapping[str, Any]
    payload: Mapping[str, Any]
    semantic_identity: str


def _issue(pointer: str, rule: str, message: str) -> ValidationError:
    return ValidationError(message, issues=(Issue(pointer, rule, message),))


def _mapping(value: object, *, pointer: str, nonempty: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict) or (nonempty and not value):
        raise _issue(pointer, "type", "Expected a nonempty JSON object" if nonempty else "Expected a JSON object")
    if not all(isinstance(key, str) and key for key in value):
        raise _issue(pointer, "keys", "JSON object keys must be nonempty strings")
    return dict(value)


def _array(value: object, *, pointer: str, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list) or (nonempty and not value):
        raise _issue(pointer, "type", "Expected a nonempty JSON array" if nonempty else "Expected a JSON array")
    return list(value)


def _string(value: object, *, pointer: str) -> str:
    if not isinstance(value, str) or not value:
        raise _issue(pointer, "required", "Expected a nonempty string")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], *, pointer: str) -> None:
    if set(value) != expected:
        raise _issue(pointer, "shape", "Fixture object has an unsupported shape")


def _canonical(value: Any, *, pointer: str = "/") -> Any:
    """Validate JSON-compatible material before deterministic rendering.

    ``loads_strict`` creates :class:`~decimal.Decimal` values.  Accepting that
    type here preserves formatting-independent numeric semantic identity while
    rejecting Python implementation values that never occur in a strict JSON
    fixture.
    """

    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise _issue(pointer, "finite", "Numeric values must be finite")
        return value
    if isinstance(value, float):
        raise _issue(pointer, "type", "Fixture numbers must be parsed from strict JSON")
    if isinstance(value, list):
        return [_canonical(item, pointer=f"{pointer}/{index}") for index, item in enumerate(value)]
    if isinstance(value, dict):
        if not all(isinstance(key, str) and key for key in value):
            raise _issue(pointer, "keys", "Fixture object keys must be nonempty strings")
        return {
            key: _canonical(item, pointer=f"{pointer}/{key}")
            for key, item in value.items()
        }
    raise _issue(pointer, "type", "Fixture contains an unsupported JSON value")


def _digest(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(dumps_strict(_canonical(dict(material))).encode("utf-8")).hexdigest()


def _sorted_unique_rows(rows: Sequence[object], *, pointer: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    rendered: set[str] = set()
    for index, raw in enumerate(rows):
        row = _mapping(raw, pointer=f"{pointer}/{index}", nonempty=True)
        canonical = _canonical(row, pointer=f"{pointer}/{index}")
        assert isinstance(canonical, dict)
        text = dumps_strict(canonical)
        if text in rendered:
            raise _issue(f"{pointer}/{index}", "duplicate", "Fixture observation is duplicated")
        rendered.add(text)
        normalized.append(canonical)
    return sorted(normalized, key=dumps_strict)


def ensure_complete_capture(capture_state: object, *, pointer: str = "/capture_state") -> None:
    """Reject states that can never be mistaken for semantic no-change.

    The importer uses this before calling the coordinator.  A partial response,
    timeout, rate limit, or parser error must surface as failure/rejection and
    can neither emit ``unchanged`` nor authorize a tombstone.
    """

    state = _string(capture_state, pointer=pointer)
    if state in _FAILED_CAPTURE_STATES:
        raise _issue(pointer, "capture_state", "Incomplete acquisition cannot be treated as unchanged")
    if state != "complete":
        raise _issue(pointer, "capture_state", "Only a complete acquisition is publishable")


def semantic_identity_generic(
    *,
    family: str,
    request_scope: Mapping[str, Any],
    normalized_payload: Mapping[str, Any],
) -> str:
    """Hash a generic scope-bearing, normalized macro candidate."""

    if family not in _FAMILIES:
        raise _issue("/family", "enum", "Unsupported Stage 3 macro family")
    scope = _mapping(request_scope, pointer="/request_scope", nonempty=True)
    payload = _mapping(normalized_payload, pointer="/payload", nonempty=True)
    return _digest({"family": family, "request_scope": scope, "payload": payload})


def semantic_identity_bls(
    *,
    response: Mapping[str, Any],
    series: Sequence[object],
    releases: Sequence[object],
    observations: Sequence[object],
    request_scope: Mapping[str, Any],
) -> str:
    """BLS identity excludes *only* top-level ``responseTime``.

    Provider messages, every other response field, the generic canonical
    rows, and request scope remain identity-bearing.  Removing a broader
    class of volatile fields would be a semantic change and is intentionally
    rejected by construction.
    """

    raw = _mapping(response, pointer="/payload/response", nonempty=True)
    if "responseTime" not in raw:
        raise _issue("/payload/response/responseTime", "required", "BLS responseTime is required for the reviewed exclusion")
    _string(raw["responseTime"], pointer="/payload/response/responseTime")
    material = {key: value for key, value in raw.items() if key != "responseTime"}
    return _digest(
        {
            "family": "bls",
            "request_scope": _mapping(request_scope, pointer="/request_scope", nonempty=True),
            "response": material,
            "series": _array(series, pointer="/payload/series"),
            "releases": _array(releases, pointer="/payload/releases"),
            "observations": _array(observations, pointer="/payload/observations"),
        }
    )


def semantic_identity_bea(
    *,
    parsed_table: Mapping[str, Any],
    series: Sequence[object],
    releases: Sequence[object],
    observations: Sequence[object],
    request_scope: Mapping[str, Any],
) -> str:
    """BEA identity excludes *only* parsed-table ``UTCProductionTime``."""

    raw = _mapping(parsed_table, pointer="/payload/parsed_table", nonempty=True)
    if "UTCProductionTime" not in raw:
        raise _issue("/payload/parsed_table/UTCProductionTime", "required", "BEA UTCProductionTime is required for the reviewed exclusion")
    _string(raw["UTCProductionTime"], pointer="/payload/parsed_table/UTCProductionTime")
    material = {key: value for key, value in raw.items() if key != "UTCProductionTime"}
    return _digest(
        {
            "family": "bea",
            "request_scope": _mapping(request_scope, pointer="/request_scope", nonempty=True),
            "parsed_table": material,
            "series": _array(series, pointer="/payload/series"),
            "releases": _array(releases, pointer="/payload/releases"),
            "observations": _array(observations, pointer="/payload/observations"),
        }
    )


def _assert_summary_only(value: Any, *, pointer: str) -> None:
    """Fail closed on CUSIP/security/holding-shaped SOMA material."""

    if isinstance(value, dict):
        for key, item in value.items():
            token = key.lower().replace("-", "_")
            if any(marker in token for marker in _SECURITY_SHAPED_TOKENS):
                raise _issue(pointer, "soma_summary_only", "SOMA fixtures cannot contain security-level fields")
            _assert_summary_only(item, pointer=f"{pointer}/{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_summary_only(item, pointer=f"{pointer}/{index}")
        return
    if isinstance(value, str) and _CUSIP_VALUE.fullmatch(value):
        raise _issue(pointer, "soma_summary_only", "SOMA fixtures cannot contain CUSIP-shaped values")


def semantic_identity_soma(
    *, latest_summary_release: Mapping[str, Any], request_scope: Mapping[str, Any]
) -> str:
    """Hash one deterministic latest summary-only SOMA release."""

    release = _mapping(latest_summary_release, pointer="/payload/latest_summary_release", nonempty=True)
    required = {"release_id", "as_of_date", "components"}
    if not required.issubset(release):
        raise _issue("/payload/latest_summary_release", "shape", "SOMA release must identify one summary release and components")
    _string(release["release_id"], pointer="/payload/latest_summary_release/release_id")
    _string(release["as_of_date"], pointer="/payload/latest_summary_release/as_of_date")
    components = _array(release["components"], pointer="/payload/latest_summary_release/components", nonempty=True)
    _assert_summary_only(release, pointer="/payload/latest_summary_release")
    normalized = dict(release)
    normalized["components"] = _sorted_unique_rows(components, pointer="/payload/latest_summary_release/components")
    scope = _mapping(request_scope, pointer="/request_scope", nonempty=True)
    if scope.get("summary_only") is not True:
        raise _issue("/request_scope/summary_only", "const", "SOMA scope must explicitly be summary-only")
    _assert_summary_only(scope, pointer="/request_scope")
    return _digest({"family": "soma", "request_scope": scope, "latest_summary_release": normalized})


def semantic_identity_eia_weekly(
    *, request_scope: Mapping[str, Any], rows: Sequence[object]
) -> str:
    """Hash requested scope plus the set of EIA weekly content identities."""

    scope = _mapping(request_scope, pointer="/request_scope", nonempty=True)
    triples: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(rows):
        row = _mapping(raw, pointer=f"/payload/weekly_rows/{index}", nonempty=True)
        required = {"series_id", "period", "content_sha256"}
        if not required.issubset(row):
            raise _issue(f"/payload/weekly_rows/{index}", "shape", "EIA weekly row lacks an identity field")
        series_id = _string(row["series_id"], pointer=f"/payload/weekly_rows/{index}/series_id")
        period = _string(row["period"], pointer=f"/payload/weekly_rows/{index}/period")
        digest = _string(row["content_sha256"], pointer=f"/payload/weekly_rows/{index}/content_sha256")
        if _SHA256.fullmatch(digest) is None:
            raise _issue(f"/payload/weekly_rows/{index}/content_sha256", "format", "EIA weekly content digest must be lowercase SHA-256")
        triple = (series_id, period, digest)
        if triple in triples:
            raise _issue(f"/payload/weekly_rows/{index}", "duplicate", "EIA weekly content identity is duplicated")
        triples.add(triple)
    if not triples:
        raise _issue("/payload/weekly_rows", "min_items", "EIA weekly fixture requires one content identity")
    return _digest(
        {
            "family": "eia_weekly",
            "request_scope": scope,
            "content_identities": [
                {"series_id": series_id, "period": period, "content_sha256": digest}
                for series_id, period, digest in sorted(triples)
            ],
        }
    )


def semantic_identity_chicagofed(
    *,
    parsed_observations: Sequence[object],
    artifact_scope: Mapping[str, Any],
    request_scope: Mapping[str, Any],
) -> str:
    """Chicago Fed identity includes parsed observations and both scopes."""

    observations = _sorted_unique_rows(parsed_observations, pointer="/payload/parsed_observations")
    if not observations:
        raise _issue("/payload/parsed_observations", "min_items", "Chicago Fed fixture requires observations")
    return _digest(
        {
            "family": "chicagofed",
            "request_scope": _mapping(request_scope, pointer="/request_scope", nonempty=True),
            "artifact_scope": _mapping(artifact_scope, pointer="/payload/artifact_scope", nonempty=True),
            "parsed_observations": observations,
        }
    )


def semantic_identity_bis(
    *, observations: Sequence[object], request_scope: Mapping[str, Any]
) -> str:
    """BIS has no volatile-field exclusion: scope and all observations bind."""

    normalized = _sorted_unique_rows(observations, pointer="/payload/observations")
    if not normalized:
        raise _issue("/payload/observations", "min_items", "BIS fixture requires observations")
    return _digest(
        {
            "family": "bis",
            "request_scope": _mapping(request_scope, pointer="/request_scope", nonempty=True),
            "observations": normalized,
        }
    )


def parse_stage3_macro_fixture(fixture_id: str, payload: bytes | str) -> Stage3MacroFixtureCandidate:
    """Parse a single strict-JSON Stage 3 fixture without touching storage."""

    raw = loads_strict(payload)
    document = _mapping(raw, pointer="/", nonempty=True)
    _exact_keys(
        document,
        {
            "schema_version",
            "family",
            "provider",
            "captured_at",
            "capture_state",
            "request_scope",
            "payload",
        },
        pointer="/",
    )
    if document["schema_version"] != _FIXTURE_SCHEMA_VERSION:
        raise _issue("/schema_version", "const", "Unsupported Stage 3 macro fixture schema")
    family = _string(document["family"], pointer="/family")
    if family not in _FAMILIES:
        raise _issue("/family", "enum", "Unsupported Stage 3 macro family")
    provider = _string(document["provider"], pointer="/provider")
    captured_at = TemporalValue.parse(_string(document["captured_at"], pointer="/captured_at"), pointer="/captured_at")
    if captured_at.precision is not TemporalPrecision.DATETIME:
        raise _issue("/captured_at", "precision", "Fixture capture must be an offset-aware datetime")
    ensure_complete_capture(document["capture_state"])
    scope = _mapping(document["request_scope"], pointer="/request_scope", nonempty=True)
    normalized_payload = _mapping(document["payload"], pointer="/payload", nonempty=True)

    if family == "bls":
        if set(normalized_payload) != {"response", "series", "releases", "observations"}:
            raise _issue("/payload", "shape", "BLS fixture payload shape is invalid")
        semantic_identity = semantic_identity_bls(
            response=_mapping(normalized_payload["response"], pointer="/payload/response", nonempty=True),
            series=_array(normalized_payload["series"], pointer="/payload/series"),
            releases=_array(normalized_payload["releases"], pointer="/payload/releases"),
            observations=_array(normalized_payload["observations"], pointer="/payload/observations"),
            request_scope=scope,
        )
    elif family == "bea":
        if set(normalized_payload) != {"parsed_table", "series", "releases", "observations"}:
            raise _issue("/payload", "shape", "BEA fixture payload shape is invalid")
        semantic_identity = semantic_identity_bea(
            parsed_table=_mapping(normalized_payload["parsed_table"], pointer="/payload/parsed_table", nonempty=True),
            series=_array(normalized_payload["series"], pointer="/payload/series"),
            releases=_array(normalized_payload["releases"], pointer="/payload/releases"),
            observations=_array(normalized_payload["observations"], pointer="/payload/observations"),
            request_scope=scope,
        )
    elif family == "soma":
        if set(normalized_payload) != {"latest_summary_release", "series", "releases", "observations"}:
            raise _issue("/payload", "shape", "SOMA fixture payload shape is invalid")
        # The summary-only boundary applies to all provider and canonical
        # material, rather than merely to the projection used for identity.
        _assert_summary_only(normalized_payload, pointer="/payload")
        semantic_identity = semantic_identity_soma(
            latest_summary_release=_mapping(normalized_payload["latest_summary_release"], pointer="/payload/latest_summary_release", nonempty=True),
            request_scope=scope,
        )
    elif family == "eia_weekly":
        if set(normalized_payload) != {"weekly_rows", "series", "releases", "observations"}:
            raise _issue("/payload", "shape", "EIA weekly fixture payload shape is invalid")
        semantic_identity = semantic_identity_eia_weekly(
            request_scope=scope,
            rows=_array(normalized_payload["weekly_rows"], pointer="/payload/weekly_rows", nonempty=True),
        )
    elif family == "chicagofed":
        if set(normalized_payload) != {"parsed_observations", "artifact_scope", "series", "releases", "observations"}:
            raise _issue("/payload", "shape", "Chicago Fed fixture payload shape is invalid")
        semantic_identity = semantic_identity_chicagofed(
            parsed_observations=_array(normalized_payload["parsed_observations"], pointer="/payload/parsed_observations", nonempty=True),
            artifact_scope=_mapping(normalized_payload["artifact_scope"], pointer="/payload/artifact_scope", nonempty=True),
            request_scope=scope,
        )
    elif family == "bis":
        if set(normalized_payload) != {"observations", "series", "releases"}:
            raise _issue("/payload", "shape", "BIS fixture payload shape is invalid")
        semantic_identity = semantic_identity_bis(
            observations=_array(normalized_payload["observations"], pointer="/payload/observations", nonempty=True),
            request_scope=scope,
        )
    else:
        expected = _GENERIC_PAYLOAD_KEYS[family]
        if set(normalized_payload) != expected:
            raise _issue("/payload", "shape", "Macro fixture payload shape is invalid")
        _array(normalized_payload["series"], pointer="/payload/series")
        _array(normalized_payload["releases"], pointer="/payload/releases")
        _array(normalized_payload["observations"], pointer="/payload/observations")
        if family == "gdp":
            _array(normalized_payload["gdp_vintages"], pointer="/payload/gdp_vintages", nonempty=True)
        elif family == "treasury":
            _array(normalized_payload["curves"], pointer="/payload/curves", nonempty=True)
        elif family == "economic_calendar":
            _array(normalized_payload["calendar_events"], pointer="/payload/calendar_events", nonempty=True)
        elif family == "eia_retail":
            _mapping(normalized_payload["retail_snapshot"], pointer="/payload/retail_snapshot", nonempty=True)
        else:
            _array(normalized_payload["recession_periods"], pointer="/payload/recession_periods", nonempty=True)
        semantic_identity = semantic_identity_generic(
            family=family,
            request_scope=scope,
            normalized_payload=normalized_payload,
        )

    return Stage3MacroFixtureCandidate(
        fixture_id=_string(fixture_id, pointer="/fixture_id"),
        family=family,
        provider=provider,
        captured_at=captured_at,
        request_scope=_canonical(scope, pointer="/request_scope"),
        payload=_canonical(normalized_payload, pointer="/payload"),
        semantic_identity=semantic_identity,
    )


__all__ = (
    "Stage3MacroFixtureCandidate",
    "ensure_complete_capture",
    "parse_stage3_macro_fixture",
    "semantic_identity_bea",
    "semantic_identity_bis",
    "semantic_identity_bls",
    "semantic_identity_chicagofed",
    "semantic_identity_eia_weekly",
    "semantic_identity_generic",
    "semantic_identity_soma",
)
