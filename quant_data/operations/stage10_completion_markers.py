"""Lightweight validators for sealed Stage 10 completion markers.

Stage 11 depends on the immutable Stage 10 candidate, completion, resume, and
failure-journal receipts.  Validating those small JSON markers must not import
or execute Stage 10's optional full-corpus reconciliation machinery.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType

from ..errors import ValidationError
from ..json_codec import dumps_strict


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CODE_REVISION = re.compile(r"^[0-9a-f]{7,64}$")
_ATTEMPT_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,159}$")
_FAILED_SYMBOL = re.compile(r"^[A-Za-z0-9.^-]{1,32}$")
_CANDIDATE_STATE = "private_candidate_only_no_operational_promotion"
_FAILURE_REASONS = frozenset(
    {"fmp_price_history_unavailable", "operator_authorized_prior_failure"}
)
_FAILURE_REASON_PROVENANCE = {
    "fmp_price_history_unavailable": "fmp_http_200_application_json",
    "operator_authorized_prior_failure": "stage10_operator_authorized_seed",
}
_FAILURE_REQUIRED_FIELDS = frozenset({"symbol", "reason", "provenance"})
_FAILURE_OPTIONAL_FIELDS = frozenset({"response_metadata", "response_sha256"})
_FAILURE_RESPONSE_METADATA_FIELDS = frozenset(
    {"body_byte_count", "content_type", "status"}
)
_MAX_FAILURE_RESPONSE_BYTES = 16_777_216


def _sha256_json(value: object) -> str:
    return hashlib.sha256(dumps_strict(value).encode("utf-8")).hexdigest()


def _require_digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValidationError(
            f"Stage 10 {field_name} must be a lowercase SHA-256 digest"
        )
    return value


def _require_text(value: object, field_name: str, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValidationError(f"Stage 10 {field_name} must be bounded normalized text")
    return value


def _datetime_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValidationError(f"Stage 10 {field_name} must be an aware datetime")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValidationError(f"Stage 10 {field_name} must be an aware datetime") from exc
    normalized = (
        parsed.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    if value != normalized:
        raise ValidationError(f"Stage 10 {field_name} must use canonical UTC text")
    return normalized


def _freeze_json(value: object, field_name: str) -> object:
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValidationError(f"Stage 10 {field_name} keys must be strings")
            frozen[key] = _freeze_json(child, field_name)
        return MappingProxyType(frozen)
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_json(child, field_name) for child in value)
    if isinstance(value, (str, int, bool, type(None))):
        return value
    raise ValidationError(f"Stage 10 {field_name} must be strict JSON material")


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


def normalize_stage10_failed_records(
    failed_records: object,
) -> tuple[Mapping[str, object], ...]:
    """Return the canonical, bounded Stage 10 failed-symbol manifest."""

    if not isinstance(failed_records, tuple) or len(failed_records) > 1_024:
        raise ValidationError("Stage 10 failed-record manifest must be a bounded tuple")
    normalized: list[Mapping[str, object]] = []
    for record in failed_records:
        if not isinstance(record, Mapping):
            raise ValidationError("Stage 10 failed-symbol record must be a closed mapping")
        keys = frozenset(record)
        if (
            not all(isinstance(key, str) for key in keys)
            or not _FAILURE_REQUIRED_FIELDS.issubset(keys)
            or not keys.issubset(_FAILURE_REQUIRED_FIELDS | _FAILURE_OPTIONAL_FIELDS)
        ):
            raise ValidationError("Stage 10 failed-symbol record has unapproved fields")
        symbol = _require_text(record.get("symbol"), "failed symbol", maximum=32)
        if (
            _FAILED_SYMBOL.fullmatch(symbol) is None
            or symbol in {"^RUT", "IWM"}
            or "russell" in symbol.casefold()
        ):
            raise ValidationError("Stage 10 failed symbol is invalid")
        reason = _require_text(record.get("reason"), "failure reason", maximum=64)
        provenance = _require_text(
            record.get("provenance"), "failure provenance", maximum=64
        )
        if _FAILURE_REASON_PROVENANCE.get(reason) != provenance:
            raise ValidationError("Stage 10 failed-symbol reason/provenance is invalid")

        response_metadata = record.get("response_metadata")
        response_sha256 = record.get("response_sha256")
        if reason == "fmp_price_history_unavailable":
            if not isinstance(response_metadata, Mapping) or "response_sha256" not in keys:
                raise ValidationError(
                    "Stage 10 live failed-symbol record lacks response binding"
                )
            if (
                not all(isinstance(key, str) for key in response_metadata)
                or frozenset(response_metadata) != _FAILURE_RESPONSE_METADATA_FIELDS
                or isinstance(response_metadata.get("body_byte_count"), bool)
                or not isinstance(response_metadata.get("body_byte_count"), int)
                or not 0
                <= response_metadata["body_byte_count"]
                <= _MAX_FAILURE_RESPONSE_BYTES
                or response_metadata.get("content_type") != "application/json"
                or response_metadata.get("status") != 200
            ):
                raise ValidationError(
                    "Stage 10 live failed-symbol response metadata is invalid"
                )
            material: dict[str, object] = {
                "symbol": symbol,
                "reason": reason,
                "provenance": provenance,
                "response_metadata": {
                    "body_byte_count": response_metadata["body_byte_count"],
                    "content_type": "application/json",
                    "status": 200,
                },
                "response_sha256": _require_digest(
                    response_sha256, "failed-symbol response_sha256"
                ),
            }
        else:
            if "response_metadata" in keys or "response_sha256" in keys:
                raise ValidationError(
                    "Stage 10 seeded failed-symbol record cannot retain response data"
                )
            material = {
                "symbol": symbol,
                "reason": reason,
                "provenance": provenance,
            }
        frozen = _freeze_json(material, "failed_records")
        if not isinstance(frozen, Mapping):
            raise ValidationError("Stage 10 failed-symbol record is invalid")
        normalized.append(frozen)

    ordered = tuple(sorted(normalized, key=lambda item: str(item["symbol"])))
    if len({str(item["symbol"]) for item in ordered}) != len(ordered):
        raise ValidationError(
            "Stage 10 failed-symbol manifest contains duplicate symbols"
        )
    return ordered


def stage10_failure_manifest_sha256(failed_records: object) -> str:
    normalized = normalize_stage10_failed_records(failed_records)
    return _sha256_json([_thaw_json(item) for item in normalized])


@dataclass(frozen=True, slots=True)
class Stage10CandidateReceipt:
    """Self-validating, path-free Stage 10 dependency receipt."""

    attempt_id: str
    generated_at: str
    code_revision: str
    candidate_state: str
    evidence: Mapping[str, object]
    evidence_sha256: str
    receipt_sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.attempt_id, str)
            or _ATTEMPT_ID.fullmatch(self.attempt_id) is None
        ):
            raise ValidationError(
                "Stage 10 attempt_id must be a safe bounded identifier"
            )
        _datetime_text(self.generated_at, "generated_at")
        if (
            not isinstance(self.code_revision, str)
            or _CODE_REVISION.fullmatch(self.code_revision) is None
        ):
            raise ValidationError(
                "Stage 10 code_revision must be a lowercase source revision"
            )
        if self.candidate_state != _CANDIDATE_STATE:
            raise ValidationError(
                "Stage 10 receipt cannot claim an operational promotion"
            )
        if not isinstance(self.evidence, Mapping):
            raise ValidationError(
                "Stage 10 candidate receipt requires path-free evidence"
            )
        frozen = _freeze_json(self.evidence, "candidate_evidence")
        if not isinstance(frozen, Mapping):
            raise ValidationError(
                "Stage 10 candidate receipt requires path-free evidence"
            )
        object.__setattr__(self, "evidence", frozen)
        _require_digest(self.evidence_sha256, "evidence_sha256")
        _require_digest(self.receipt_sha256, "receipt_sha256")
        evidence = _thaw_json(frozen)
        if not isinstance(evidence, Mapping):
            raise ValidationError(
                "Stage 10 candidate receipt requires path-free evidence"
            )
        material = {name: value for name, value in evidence.items() if name != "sha256"}
        if (
            evidence.get("sha256") != self.evidence_sha256
            or _sha256_json(material) != self.evidence_sha256
        ):
            raise ValidationError(
                "Stage 10 candidate receipt evidence digest is invalid"
            )
        if self.receipt_sha256 != _sha256_json(self.material()):
            raise ValidationError("Stage 10 candidate receipt digest is invalid")

    def material(self) -> dict[str, object]:
        return {
            "contract": "quant_data.stage10_candidate_receipt",
            "contract_version": "1.0.0",
            "attempt_id": self.attempt_id,
            "generated_at": self.generated_at,
            "code_revision": self.code_revision,
            "candidate_state": self.candidate_state,
            "evidence": _thaw_json(self.evidence),
            "evidence_sha256": self.evidence_sha256,
        }

    def to_primitive(self) -> dict[str, object]:
        return {**self.material(), "receipt_sha256": self.receipt_sha256}


__all__ = (
    "Stage10CandidateReceipt",
    "normalize_stage10_failed_records",
    "stage10_failure_manifest_sha256",
)
