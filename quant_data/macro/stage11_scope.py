"""Strict immutable loader for the reviewed Stage 11 macro scope.

The loader accepts one caller-supplied JSON path and produces only typed,
credential-free configuration.  It does not discover configuration, consult
the environment, open a store, construct a request with credentials, or make
network calls.  Every semantic field is closed and pinned to the reviewable
Stage 11 BEA/EIA profile.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from ..errors import Issue, ValidationError
from ..json_codec import dumps_strict, loads_strict


STAGE11_SCOPE_CONTRACT = "quant_data.stage11_macro_scope"
STAGE11_SCOPE_VERSION = "1.0.0"
STAGE11_TARGET_PROFILE_ID = "stage11_bea_eia_macro_v1"
STAGE11_TARGET_ROOT = "/home/volatility/quant-data-nonprod/stage10-fmp-market-history-v1"
STAGE11_REQUIRED_MARKET_CANDIDATE_CONTRACT = (
    "quant_data.stage10_candidate_receipt"
)
STAGE11_REQUIRED_MARKET_PROFILE_ID = "stage10_fmp_market_history_v1"

# SHA-256 of the reviewed semantic manifest rendered with ``dumps_strict``.
# Whitespace and object-key order are immaterial; no declared scope value is.
REVIEWED_STAGE11_MACRO_SCOPE_SHA256 = (
    "280d4056a2d6085449fef84c882165d53f89dc06541b4ce7611f8e33ea4f7cc1"
)

_MAX_SCOPE_BYTES = 128 * 1024
_TOKEN = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_PATH = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@/%-]*$")
_HOST = re.compile(r"^[a-z0-9][a-z0-9.-]{0,252}$")

_EXPECTED_BOUNDS = (
    ("bea_max_bytes_per_response", 8_388_608),
    ("bea_max_requests", 2),
    ("bea_max_rows_per_series", 1_000),
    ("eia_max_bytes_per_response", 16_777_216),
    ("eia_max_pages", 8),
    ("eia_max_rows_per_page", 5_000),
    ("eia_weekly_max_rows", 5_000),
    ("minimum_request_interval_milliseconds", 1_000),
)
_EXPECTED_BEA_REQUESTS = (
    ("T10101", "A191RL", "macro.gdp.real_qoq_saar_pct"),
    ("T10105", "A191RC", "macro.gdp.nominal_billions"),
)
_EXPECTED_EIA_RETAIL_DATA = (
    ("macro.eia.electricity.retail_sales", "sales"),
    ("macro.eia.electricity.retail_revenue", "revenue"),
    ("macro.eia.electricity.retail_price", "price"),
    ("macro.eia.electricity.retail_customers", "customers"),
)
_ALLOWED_ENDPOINT_PATH_POINTERS = frozenset(
    {
        "/providers/bea/requests/0/path",
        "/providers/bea/requests/1/path",
        "/providers/eia/retail/path",
        "/providers/eia/weekly/path",
    }
)
_ALLOWED_ROOT_POINTERS = frozenset({"/target_root"})
_SENSITIVE_KEY_MARKERS = (
    "secret",
    "token",
    "password",
    "credential",
    "authorization",
    "header",
    "apikey",
)


@dataclass(frozen=True, slots=True)
class Stage11Bounds:
    bea_max_bytes_per_response: int
    bea_max_requests: int
    bea_max_rows_per_series: int
    eia_max_bytes_per_response: int
    eia_max_pages: int
    eia_max_rows_per_page: int
    eia_weekly_max_rows: int
    minimum_request_interval_milliseconds: int

    def manifest_mapping(self) -> dict[str, int]:
        return {
            "bea_max_bytes_per_response": self.bea_max_bytes_per_response,
            "bea_max_requests": self.bea_max_requests,
            "bea_max_rows_per_series": self.bea_max_rows_per_series,
            "eia_max_bytes_per_response": self.eia_max_bytes_per_response,
            "eia_max_pages": self.eia_max_pages,
            "eia_max_rows_per_page": self.eia_max_rows_per_page,
            "eia_weekly_max_rows": self.eia_weekly_max_rows,
            "minimum_request_interval_milliseconds": self.minimum_request_interval_milliseconds,
        }


@dataclass(frozen=True, slots=True)
class Stage11Dependency:
    required_market_candidate_contract: str
    required_market_profile_id: str

    def manifest_mapping(self) -> dict[str, str]:
        return {
            "required_market_candidate_contract": self.required_market_candidate_contract,
            "required_market_profile_id": self.required_market_profile_id,
        }


@dataclass(frozen=True, slots=True)
class Stage11BeaSeries:
    canonical_series_id: str
    provider_series_code: str

    def manifest_mapping(self) -> dict[str, str]:
        return {
            "canonical_series_id": self.canonical_series_id,
            "provider_series_code": self.provider_series_code,
        }


@dataclass(frozen=True, slots=True)
class Stage11BeaRequest:
    dataset_name: str
    frequency: str
    method: str
    path: str
    result_format: str
    series: tuple[Stage11BeaSeries, ...]
    table_name: str
    year: str

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "dataset_name": self.dataset_name,
            "frequency": self.frequency,
            "method": self.method,
            "path": self.path,
            "result_format": self.result_format,
            "series": [item.manifest_mapping() for item in self.series],
            "table_name": self.table_name,
            "year": self.year,
        }


@dataclass(frozen=True, slots=True)
class Stage11BeaProvider:
    configuration_env: str
    host: str
    requests: tuple[Stage11BeaRequest, ...]

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "configuration_env": self.configuration_env,
            "host": self.host,
            "requests": [item.manifest_mapping() for item in self.requests],
        }


@dataclass(frozen=True, slots=True)
class Stage11EiaRetailData:
    canonical_series_id: str
    field: str

    def manifest_mapping(self) -> dict[str, str]:
        return {
            "canonical_series_id": self.canonical_series_id,
            "field": self.field,
        }


@dataclass(frozen=True, slots=True)
class Stage11EiaSort:
    column: str
    direction: str

    def manifest_mapping(self) -> dict[str, str]:
        return {"column": self.column, "direction": self.direction}


@dataclass(frozen=True, slots=True)
class Stage11EiaRetail:
    data: tuple[Stage11EiaRetailData, ...]
    facets: Mapping[str, tuple[str, ...]]
    frequency: str
    length: int
    path: str
    sort: tuple[Stage11EiaSort, ...]
    tombstone_authoritative: bool

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "data": [item.manifest_mapping() for item in self.data],
            "facets": {key: list(value) for key, value in self.facets.items()},
            "frequency": self.frequency,
            "length": self.length,
            "path": self.path,
            "sort": [item.manifest_mapping() for item in self.sort],
            "tombstone_authoritative": self.tombstone_authoritative,
        }


@dataclass(frozen=True, slots=True)
class Stage11EiaWeekly:
    canonical_series_id: str
    path: str
    provider_series_id: str

    def manifest_mapping(self) -> dict[str, str]:
        return {
            "canonical_series_id": self.canonical_series_id,
            "path": self.path,
            "provider_series_id": self.provider_series_id,
        }


@dataclass(frozen=True, slots=True)
class Stage11EiaProvider:
    configuration_env: str
    host: str
    retail: Stage11EiaRetail
    weekly: Stage11EiaWeekly

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "configuration_env": self.configuration_env,
            "host": self.host,
            "retail": self.retail.manifest_mapping(),
            "weekly": self.weekly.manifest_mapping(),
        }


@dataclass(frozen=True, slots=True)
class Stage11MacroScope:
    bounds: Stage11Bounds
    contract: str
    dependency: Stage11Dependency
    bea: Stage11BeaProvider
    eia: Stage11EiaProvider
    target_profile_id: str
    target_root: str
    version: str
    manifest_sha256: str

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "bounds": self.bounds.manifest_mapping(),
            "contract": self.contract,
            "dependency": self.dependency.manifest_mapping(),
            "providers": {
                "bea": self.bea.manifest_mapping(),
                "eia": self.eia.manifest_mapping(),
            },
            "target_profile_id": self.target_profile_id,
            "target_root": self.target_root,
            "version": self.version,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.manifest_mapping(), "manifest_sha256": self.manifest_sha256}


def _error(pointer: str, rule: str, message: str) -> ValidationError:
    return ValidationError(message, issues=(Issue(pointer or "/", rule, message),))


def _child(pointer: str, part: str | int) -> str:
    return f"{pointer}/{part}" if pointer else f"/{part}"


def _mapping(raw: object, *, pointer: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping) or not all(isinstance(key, str) for key in raw):
        raise _error(pointer, "type", "Expected an object with string keys")
    return raw


def _array(raw: object, *, pointer: str) -> list[Any]:
    if not isinstance(raw, list):
        raise _error(pointer, "type", "Expected an array")
    return raw


def _closed_object(raw: Mapping[str, Any], *, pointer: str, fields: frozenset[str]) -> None:
    if set(raw) != fields:
        raise _error(pointer, "shape", "Object fields do not match the reviewed Stage 11 scope")


def _text(raw: object, *, pointer: str, maximum: int = 256) -> str:
    if (
        not isinstance(raw, str)
        or not raw
        or raw != raw.strip()
        or len(raw) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in raw)
    ):
        raise _error(pointer, "type", "Expected bounded nonempty trimmed text")
    return raw


def _exact_text(raw: object, *, pointer: str, expected: str) -> str:
    value = _text(raw, pointer=pointer, maximum=max(256, len(expected)))
    if value != expected:
        raise _error(pointer, "constant", "Value differs from the reviewed Stage 11 scope")
    return value


def _exact_int(raw: object, *, pointer: str, expected: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw != expected:
        raise _error(pointer, "constant", "Integer differs from the reviewed Stage 11 scope")
    return raw


def _exact_bool(raw: object, *, pointer: str, expected: bool) -> bool:
    if not isinstance(raw, bool) or raw is not expected:
        raise _error(pointer, "constant", "Boolean differs from the reviewed Stage 11 scope")
    return raw


def _normalized_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.casefold())


def _path_like_key(normalized: str) -> bool:
    """Recognize real path declarations without rejecting ``profile`` et al."""

    return (
        normalized in {"url", "uri", "file", "filename", "filepath", "directory", "directorypath"}
        or normalized.endswith("path")
        or normalized.endswith("url")
        or normalized.endswith("uri")
    )


def _reject_sensitive_or_path_like_keys(raw: object, *, pointer: str) -> None:
    """Reject unreviewed secret, URL, filesystem, and endpoint declarations.

    Endpoint ``path`` is intentionally allowed only at the four reviewed
    provider request locations.  ``target_root`` is the single reviewed local
    target declaration; every other root-like key is rejected before schema
    parsing can normalize it away.
    """

    if isinstance(raw, Mapping):
        for key, value in raw.items():
            if not isinstance(key, str):
                raise _error(pointer, "type", "Object keys must be strings")
            child = _child(pointer, key)
            normalized = _normalized_key(key)
            if any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS):
                raise _error(child, "forbidden_key", "Secrets and request credentials are not scope fields")
            if normalized == "path" or normalized.endswith("path"):
                if child not in _ALLOWED_ENDPOINT_PATH_POINTERS:
                    raise _error(child, "forbidden_key", "Only reviewed endpoint paths are allowed")
            elif _path_like_key(normalized):
                raise _error(child, "forbidden_key", "URLs and file declarations are not scope fields")
            elif "root" in normalized and child not in _ALLOWED_ROOT_POINTERS:
                raise _error(child, "forbidden_key", "Only the reviewed target root is allowed")
            _reject_sensitive_or_path_like_keys(value, pointer=child)
    elif isinstance(raw, list):
        for index, value in enumerate(raw):
            _reject_sensitive_or_path_like_keys(value, pointer=_child(pointer, index))


def _validate_endpoint_path(raw: object, *, pointer: str, expected: str) -> str:
    value = _exact_text(raw, pointer=pointer, expected=expected)
    if _PATH.fullmatch(value) is None or "?" in value or "#" in value or "//" in value:
        raise _error(pointer, "format", "Endpoint path is not a reviewed absolute path")
    return value


def _validate_host(raw: object, *, pointer: str, expected: str) -> str:
    value = _exact_text(raw, pointer=pointer, expected=expected)
    if _HOST.fullmatch(value) is None or "/" in value or ".." in value:
        raise _error(pointer, "format", "Provider host is invalid")
    return value


def _validate_env(raw: object, *, pointer: str, expected: str) -> str:
    value = _exact_text(raw, pointer=pointer, expected=expected)
    if _ENVIRONMENT_NAME.fullmatch(value) is None:
        raise _error(pointer, "format", "Provider environment variable name is invalid")
    return value


def _validate_token(raw: object, *, pointer: str, expected: str) -> str:
    value = _exact_text(raw, pointer=pointer, expected=expected)
    if _TOKEN.fullmatch(value) is None:
        raise _error(pointer, "format", "Reviewed identifier is invalid")
    return value


def _parse_bounds(raw: object) -> Stage11Bounds:
    pointer = "/bounds"
    value = _mapping(raw, pointer=pointer)
    _closed_object(value, pointer=pointer, fields=frozenset(name for name, _ in _EXPECTED_BOUNDS))
    parsed = {
        name: _exact_int(value[name], pointer=_child(pointer, name), expected=expected)
        for name, expected in _EXPECTED_BOUNDS
    }
    return Stage11Bounds(**parsed)


def _parse_dependency(raw: object) -> Stage11Dependency:
    pointer = "/dependency"
    value = _mapping(raw, pointer=pointer)
    _closed_object(
        value,
        pointer=pointer,
        fields=frozenset({"required_market_candidate_contract", "required_market_profile_id"}),
    )
    return Stage11Dependency(
        required_market_candidate_contract=_exact_text(
            value["required_market_candidate_contract"],
            pointer=_child(pointer, "required_market_candidate_contract"),
            expected=STAGE11_REQUIRED_MARKET_CANDIDATE_CONTRACT,
        ),
        required_market_profile_id=_validate_token(
            value["required_market_profile_id"],
            pointer=_child(pointer, "required_market_profile_id"),
            expected=STAGE11_REQUIRED_MARKET_PROFILE_ID,
        ),
    )


def _parse_bea_request(raw: object, *, pointer: str, expected: tuple[str, str, str]) -> Stage11BeaRequest:
    table_name, series_code, canonical_series_id = expected
    value = _mapping(raw, pointer=pointer)
    _closed_object(
        value,
        pointer=pointer,
        fields=frozenset(
            {
                "dataset_name",
                "frequency",
                "method",
                "path",
                "result_format",
                "series",
                "table_name",
                "year",
            }
        ),
    )
    series_raw = _array(value["series"], pointer=_child(pointer, "series"))
    if len(series_raw) != 1:
        raise _error(_child(pointer, "series"), "count", "BEA request must have exactly one reviewed series")
    series_value = _mapping(series_raw[0], pointer=_child(_child(pointer, "series"), 0))
    _closed_object(
        series_value,
        pointer=_child(_child(pointer, "series"), 0),
        fields=frozenset({"canonical_series_id", "provider_series_code"}),
    )
    return Stage11BeaRequest(
        dataset_name=_exact_text(value["dataset_name"], pointer=_child(pointer, "dataset_name"), expected="NIPA"),
        frequency=_exact_text(value["frequency"], pointer=_child(pointer, "frequency"), expected="Q"),
        method=_exact_text(value["method"], pointer=_child(pointer, "method"), expected="GetData"),
        path=_validate_endpoint_path(value["path"], pointer=_child(pointer, "path"), expected="/api/data/"),
        result_format=_exact_text(value["result_format"], pointer=_child(pointer, "result_format"), expected="JSON"),
        series=(
            Stage11BeaSeries(
                canonical_series_id=_validate_token(
                    series_value["canonical_series_id"],
                    pointer=_child(_child(_child(pointer, "series"), 0), "canonical_series_id"),
                    expected=canonical_series_id,
                ),
                provider_series_code=_exact_text(
                    series_value["provider_series_code"],
                    pointer=_child(_child(_child(pointer, "series"), 0), "provider_series_code"),
                    expected=series_code,
                ),
            ),
        ),
        table_name=_exact_text(value["table_name"], pointer=_child(pointer, "table_name"), expected=table_name),
        year=_exact_text(value["year"], pointer=_child(pointer, "year"), expected="ALL"),
    )


def _parse_bea(raw: object) -> Stage11BeaProvider:
    pointer = "/providers/bea"
    value = _mapping(raw, pointer=pointer)
    _closed_object(value, pointer=pointer, fields=frozenset({"configuration_env", "host", "requests"}))
    requests_raw = _array(value["requests"], pointer=_child(pointer, "requests"))
    if len(requests_raw) != len(_EXPECTED_BEA_REQUESTS):
        raise _error(_child(pointer, "requests"), "count", "BEA scope requires exactly two reviewed requests")
    requests = tuple(
        _parse_bea_request(
            request,
            pointer=_child(_child(pointer, "requests"), index),
            expected=_EXPECTED_BEA_REQUESTS[index],
        )
        for index, request in enumerate(requests_raw)
    )
    if tuple(
        (request.table_name, request.series[0].provider_series_code, request.series[0].canonical_series_id)
        for request in requests
    ) != _EXPECTED_BEA_REQUESTS:
        raise _error(_child(pointer, "requests"), "immutable", "BEA requests differ from the reviewed scope")
    return Stage11BeaProvider(
        configuration_env=_validate_env(value["configuration_env"], pointer=_child(pointer, "configuration_env"), expected="BEA_API_KEY"),
        host=_validate_host(value["host"], pointer=_child(pointer, "host"), expected="apps.bea.gov"),
        requests=requests,
    )


def _parse_eia_retail(raw: object) -> Stage11EiaRetail:
    pointer = "/providers/eia/retail"
    value = _mapping(raw, pointer=pointer)
    _closed_object(
        value,
        pointer=pointer,
        fields=frozenset({"data", "facets", "frequency", "length", "path", "sort", "tombstone_authoritative"}),
    )
    data_raw = _array(value["data"], pointer=_child(pointer, "data"))
    if len(data_raw) != len(_EXPECTED_EIA_RETAIL_DATA):
        raise _error(_child(pointer, "data"), "count", "EIA retail scope requires exactly four reviewed metrics")
    data: list[Stage11EiaRetailData] = []
    for index, expected in enumerate(_EXPECTED_EIA_RETAIL_DATA):
        row_pointer = _child(_child(pointer, "data"), index)
        row = _mapping(data_raw[index], pointer=row_pointer)
        _closed_object(row, pointer=row_pointer, fields=frozenset({"canonical_series_id", "field"}))
        data.append(
            Stage11EiaRetailData(
                canonical_series_id=_validate_token(row["canonical_series_id"], pointer=_child(row_pointer, "canonical_series_id"), expected=expected[0]),
                field=_exact_text(row["field"], pointer=_child(row_pointer, "field"), expected=expected[1]),
            )
        )
    if tuple((item.canonical_series_id, item.field) for item in data) != _EXPECTED_EIA_RETAIL_DATA:
        raise _error(_child(pointer, "data"), "immutable", "EIA retail metrics differ from the reviewed scope")
    facets_pointer = _child(pointer, "facets")
    facets_raw = _mapping(value["facets"], pointer=facets_pointer)
    _closed_object(facets_raw, pointer=facets_pointer, fields=frozenset({"sectorid", "stateid"}))
    facets: dict[str, tuple[str, ...]] = {}
    for name, expected in (("sectorid", ("ALL",)), ("stateid", ("US",))):
        facet_pointer = _child(facets_pointer, name)
        entries = _array(facets_raw[name], pointer=facet_pointer)
        values = tuple(_text(item, pointer=_child(facet_pointer, index)) for index, item in enumerate(entries))
        if values != expected:
            raise _error(facet_pointer, "constant", "EIA retail facet differs from the reviewed scope")
        facets[name] = values
    sort_raw = _array(value["sort"], pointer=_child(pointer, "sort"))
    if len(sort_raw) != 1:
        raise _error(_child(pointer, "sort"), "count", "EIA retail requires one reviewed sort clause")
    sort_pointer = _child(_child(pointer, "sort"), 0)
    sort_value = _mapping(sort_raw[0], pointer=sort_pointer)
    _closed_object(sort_value, pointer=sort_pointer, fields=frozenset({"column", "direction"}))
    sort = (
        Stage11EiaSort(
            column=_exact_text(sort_value["column"], pointer=_child(sort_pointer, "column"), expected="period"),
            direction=_exact_text(sort_value["direction"], pointer=_child(sort_pointer, "direction"), expected="asc"),
        ),
    )
    return Stage11EiaRetail(
        data=tuple(data),
        facets=MappingProxyType(facets),
        frequency=_exact_text(value["frequency"], pointer=_child(pointer, "frequency"), expected="monthly"),
        length=_exact_int(value["length"], pointer=_child(pointer, "length"), expected=5_000),
        path=_validate_endpoint_path(value["path"], pointer=_child(pointer, "path"), expected="/v2/electricity/retail-sales/data"),
        sort=sort,
        tombstone_authoritative=_exact_bool(value["tombstone_authoritative"], pointer=_child(pointer, "tombstone_authoritative"), expected=False),
    )


def _parse_eia_weekly(raw: object) -> Stage11EiaWeekly:
    pointer = "/providers/eia/weekly"
    value = _mapping(raw, pointer=pointer)
    _closed_object(value, pointer=pointer, fields=frozenset({"canonical_series_id", "path", "provider_series_id"}))
    return Stage11EiaWeekly(
        canonical_series_id=_validate_token(value["canonical_series_id"], pointer=_child(pointer, "canonical_series_id"), expected="macro.eia.weekly.petroleum_stock"),
        path=_validate_endpoint_path(value["path"], pointer=_child(pointer, "path"), expected="/v2/seriesid/PET.WCESTUS1.W"),
        provider_series_id=_exact_text(value["provider_series_id"], pointer=_child(pointer, "provider_series_id"), expected="PET.WCESTUS1.W"),
    )


def _parse_eia(raw: object) -> Stage11EiaProvider:
    pointer = "/providers/eia"
    value = _mapping(raw, pointer=pointer)
    _closed_object(value, pointer=pointer, fields=frozenset({"configuration_env", "host", "retail", "weekly"}))
    return Stage11EiaProvider(
        configuration_env=_validate_env(value["configuration_env"], pointer=_child(pointer, "configuration_env"), expected="EIA_API_KEY"),
        host=_validate_host(value["host"], pointer=_child(pointer, "host"), expected="api.eia.gov"),
        retail=_parse_eia_retail(value["retail"]),
        weekly=_parse_eia_weekly(value["weekly"]),
    )


def _parse_scope(raw: object) -> Stage11MacroScope:
    value = _mapping(raw, pointer="/")
    _closed_object(
        value,
        pointer="/",
        fields=frozenset({"bounds", "contract", "dependency", "providers", "target_profile_id", "target_root", "version"}),
    )
    providers = _mapping(value["providers"], pointer="/providers")
    _closed_object(providers, pointer="/providers", fields=frozenset({"bea", "eia"}))
    scope = Stage11MacroScope(
        bounds=_parse_bounds(value["bounds"]),
        contract=_exact_text(value["contract"], pointer="/contract", expected=STAGE11_SCOPE_CONTRACT),
        dependency=_parse_dependency(value["dependency"]),
        bea=_parse_bea(providers["bea"]),
        eia=_parse_eia(providers["eia"]),
        target_profile_id=_validate_token(value["target_profile_id"], pointer="/target_profile_id", expected=STAGE11_TARGET_PROFILE_ID),
        target_root=_exact_text(value["target_root"], pointer="/target_root", expected=STAGE11_TARGET_ROOT, ),
        version=_exact_text(value["version"], pointer="/version", expected=STAGE11_SCOPE_VERSION),
        manifest_sha256="",
    )
    if dumps_strict(value) != dumps_strict(scope.manifest_mapping()):
        raise _error("/", "canonical", "Scope cannot be normalized to the reviewed closed schema")
    digest = hashlib.sha256(dumps_strict(scope.manifest_mapping()).encode("utf-8")).hexdigest()
    if digest != REVIEWED_STAGE11_MACRO_SCOPE_SHA256:
        raise _error("/", "immutable", "Scope differs from the reviewed immutable Stage 11 manifest")
    return replace(scope, manifest_sha256=digest)


def load_stage11_macro_scope(path: str | Path) -> Stage11MacroScope:
    """Load exactly one explicit, immutable Stage 11 macro-scope JSON file."""

    if isinstance(path, bool) or not isinstance(path, (str, Path)):
        raise _error("/", "path", "An explicit Stage 11 scope path is required")
    if isinstance(path, str) and not path.strip():
        raise _error("/", "path", "An explicit Stage 11 scope path is required")
    try:
        payload = Path(path).read_bytes()
    except (OSError, ValueError) as exc:
        raise _error("/", "path", "Stage 11 scope file is unavailable") from exc
    raw = loads_strict(payload, max_bytes=_MAX_SCOPE_BYTES)
    _reject_sensitive_or_path_like_keys(raw, pointer="")
    return _parse_scope(raw)


__all__ = (
    "REVIEWED_STAGE11_MACRO_SCOPE_SHA256",
    "STAGE11_REQUIRED_MARKET_CANDIDATE_CONTRACT",
    "STAGE11_REQUIRED_MARKET_PROFILE_ID",
    "STAGE11_SCOPE_CONTRACT",
    "STAGE11_SCOPE_VERSION",
    "STAGE11_TARGET_PROFILE_ID",
    "STAGE11_TARGET_ROOT",
    "Stage11BeaProvider",
    "Stage11BeaRequest",
    "Stage11BeaSeries",
    "Stage11Bounds",
    "Stage11Dependency",
    "Stage11EiaProvider",
    "Stage11EiaRetail",
    "Stage11EiaRetailData",
    "Stage11EiaSort",
    "Stage11EiaWeekly",
    "Stage11MacroScope",
    "load_stage11_macro_scope",
)
