"""Host-owned, side-effect-free execution context for the Stage 5 tool core."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from time import monotonic_ns
from typing import Any, Callable, Protocol

from quant_data.errors import (
    CancellationError,
    CapabilityUnavailableError,
    DeadlineExceededError,
    ResourceLimitError,
    ValidationError,
)
from quant_data.json_codec import MAX_JSON_BYTES
from quant_data.stores import StoreMap


@dataclass(frozen=True, slots=True)
class ExecutionBudget:
    max_rows: int = 10_000
    max_series: int = 20
    max_operations: int = 5_000_000
    max_output_bytes: int = MAX_JSON_BYTES

    def __post_init__(self) -> None:
        for name, value, upper in (
            ("max_rows", self.max_rows, 200_000),
            ("max_series", self.max_series, 20),
            ("max_operations", self.max_operations, 5_000_000),
            ("max_output_bytes", self.max_output_bytes, MAX_JSON_BYTES),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
                raise ValidationError(f"Execution budget {name} is invalid")

    def require(self, *, rows: int = 0, series: int = 0, operations: int = 0) -> None:
        if rows > self.max_rows or series > self.max_series or operations > self.max_operations:
            raise ResourceLimitError("Tool workload exceeds its host-owned execution budget")


@dataclass(frozen=True, slots=True)
class CapabilitySet:
    enabled: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if any(not isinstance(item, str) or not item for item in self.enabled):
            raise ValidationError("Capability identifiers must be nonempty strings")

    def require(self, capability_id: str) -> None:
        if capability_id not in self.enabled:
            raise CapabilityUnavailableError(
                "The host does not enable this capability",
                capability_id=capability_id,
            )


@dataclass(frozen=True, slots=True)
class CancellationToken:
    cancelled: bool = False

    def check(self) -> None:
        if self.cancelled:
            raise CancellationError("The host cancelled this read-only operation")


@dataclass(frozen=True, slots=True)
class Deadline:
    expires_at: Decimal | None = None

    def __post_init__(self) -> None:
        if self.expires_at is not None and not self.expires_at.is_finite():
            raise ValidationError("Execution deadline must be finite")

    def check(self, now: Decimal) -> None:
        if not now.is_finite():
            raise ValidationError("Execution clock returned a non-finite value")
        if self.expires_at is not None and now >= self.expires_at:
            raise DeadlineExceededError("The host-owned execution deadline elapsed")


class Clock(Protocol):
    def monotonic(self) -> Decimal: ...

    def instant(self) -> str: ...


@dataclass(frozen=True, slots=True)
class FixedClock:
    monotonic_value: Decimal
    instant_value: str

    def __post_init__(self) -> None:
        if not self.monotonic_value.is_finite() or not self.instant_value:
            raise ValidationError("Fixed execution clock is invalid")

    def monotonic(self) -> Decimal:
        return self.monotonic_value

    def instant(self) -> str:
        return self.instant_value


@dataclass(frozen=True, slots=True)
class HostClock:
    """Host-owned monotonic deadline clock, never derived from public input."""

    def monotonic(self) -> Decimal:
        return Decimal(monotonic_ns()) / Decimal(1_000_000_000)

    def instant(self) -> str:
        return (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )


class ReceiptSink(Protocol):
    def __call__(self, receipt: Any) -> None: ...


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    """Everything an operation receives from the host, never from public JSON."""

    store_map: StoreMap
    registry_revision: str
    registry_sha256: str
    request_id: str
    api_version: str
    tool_name: str
    tool_version: str
    operation_graph_id: str
    operation_version: str
    budget: ExecutionBudget
    capabilities: CapabilitySet
    deadline: Deadline
    cancellation: CancellationToken
    clock: Clock
    receipt_sink: ReceiptSink | None = None
    execution: str = "offline_fixture"

    def __post_init__(self) -> None:
        for value in (
            self.registry_revision,
            self.registry_sha256,
            self.request_id,
            self.api_version,
            self.tool_name,
            self.tool_version,
            self.operation_graph_id,
            self.operation_version,
        ):
            if not isinstance(value, str) or not value:
                raise ValidationError("Tool execution context identity is incomplete")
        if self.execution != "offline_fixture":
            raise ValidationError("Stage 5 execution must remain offline_fixture")

    def checkpoint(self) -> None:
        self.cancellation.check()
        self.deadline.check(self.clock.monotonic())

    def emit(self, receipt: Any) -> None:
        if self.receipt_sink is not None:
            self.receipt_sink(receipt)


OperationCallable = Callable[[ToolExecutionContext, Any], Any]


__all__ = (
    "CapabilitySet",
    "CancellationToken",
    "Clock",
    "Deadline",
    "ExecutionBudget",
    "FixedClock",
    "HostClock",
    "OperationCallable",
    "ReceiptSink",
    "ToolExecutionContext",
)
