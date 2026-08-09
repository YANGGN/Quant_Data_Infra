"""Structured, bounded error types shared by the Stage 1 slice."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class Issue:
    pointer: str
    rule: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "pointer": self.pointer,
            "rule": self.rule,
            "message": self.message,
        }


class QuantDataError(Exception):
    """Base exception safe to adapt into a public structured envelope."""

    code = "quant_data_error"
    http_status = 400

    def __init__(
        self,
        message: str,
        *,
        issues: Iterable[Issue] = (),
        code: str | None = None,
    ) -> None:
        safe_message = str(message).strip()[:500] or "Request failed"
        super().__init__(safe_message)
        self.safe_message = safe_message
        self.issues = tuple(issues)[:32]
        if code is not None:
            self.code = code

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "code": self.code,
            "message": self.safe_message,
        }
        if self.issues:
            result["issues"] = [issue.to_dict() for issue in self.issues]
        return result


class ValidationError(QuantDataError):
    code = "invalid_request"
    http_status = 400


class RegistryError(QuantDataError):
    code = "invalid_registry"
    http_status = 500


class MigrationError(QuantDataError):
    code = "migration_integrity_error"
    http_status = 500


class StoreUnavailableError(QuantDataError):
    code = "store_unavailable"
    http_status = 503


class ConflictError(QuantDataError):
    code = "conflict"
    http_status = 409


class ResourceLimitError(QuantDataError):
    code = "resource_limit"
    http_status = 413
