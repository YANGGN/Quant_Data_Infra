"""Restricted one-shot wrapper for the approved Stage 9 FMP SPY backfill.

The command deliberately has no general backfill mode.  It accepts only the
reviewed project and nonproduction target roots, receives the FMP credential
only through ``FMP_API_KEY``, and leaves a candidate-only receipt.  All live
provider work is confined to the single primary importer preparation; the
separate correction rehearsal operates on an isolated restored cohort through
an injected, response-only seam.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, TextIO

from ..errors import (
    CapabilityUnavailableError,
    ConflictError,
    MigrationError,
    RegistryError,
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from ..json_codec import dumps_strict, loads_strict
from ..migrations import initialize_all
from ..registry import CANONICAL_REGISTRY_PATH, Registry, load_registry, stage9_registry_profile
from ..stores import StoreMap, StoreRole, read_connection
from .backup import backup_all, restore_all
from .repopulation import (
    FMP_CANONICAL_DATASET_ID,
    FMP_COLLECTOR_ID,
    FMP_CURRENCY_SEGMENT,
    FMP_ENDPOINT_PATH,
    FMP_EVIDENCE_DATASET_ID,
    FMP_IDENTITY_DATASET_ID,
    FMP_MIGRATION_ID,
    FMP_PRICE_VARIANT,
    FMP_PROVIDER,
    FMP_REQUEST_END_DATE,
    FMP_REQUEST_START_DATE,
    FMP_SYMBOL,
    PrivatePromotionCandidateState,
    build_repopulation_evidence,
    publish_promotion_candidate,
    reconcile_fmp_spy_market,
)


APPROVED_PROJECT_ROOT = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
APPROVED_TARGET_ROOT = Path(
    "/home/volatility/quant-data-nonprod/stage9-fmp-spy-202607"
)
_ATTEMPT_ID = "stage9-fmp-spy-202607"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class _ArgumentFailure(Exception):
    """A CLI-only failure that must not expose argparse diagnostics."""


class _ConfigurationFailure(Exception):
    """A safe preflight failure for environment or reviewed registry drift."""


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _ArgumentFailure


@dataclass(frozen=True, slots=True)
class BackfillScope:
    """The only permitted provider request scope for this command."""

    symbol: str = FMP_SYMBOL
    start_date: str = FMP_REQUEST_START_DATE
    end_date: str = FMP_REQUEST_END_DATE
    price_variant: str = FMP_PRICE_VARIANT
    currency_segment: str = FMP_CURRENCY_SEGMENT
    provider: str = FMP_PROVIDER
    endpoint_path: str = FMP_ENDPOINT_PATH

    def request_scope(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "price_variant": self.price_variant,
            "currency_segment": self.currency_segment,
            "provider": self.provider,
            "endpoint_path": self.endpoint_path,
        }


@dataclass(frozen=True, slots=True)
class CandidateProvenance:
    """Private primary-candidate facts needed for later nonproduction proof."""

    request_scope: Mapping[str, object]
    response_sha256: str
    semantic_identity: str
    response: object


@dataclass(slots=True)
class _LiveTransportContext:
    api_key: str = field(repr=False)
    transport: object
    response: object | None = None


@dataclass(slots=True)
class _LiveImporterContext:
    importer: object
    transport_context: _LiveTransportContext


class _ResponseTeeTransport:
    """Retain exactly one already-validated live response only in memory."""

    def __init__(self, delegate: object) -> None:
        self._delegate = delegate
        self.response: object | None = None

    def _validate_stage9_live_store_map(
        self, store_map: StoreMap
    ) -> object:
        validator = getattr(self._delegate, "_validate_stage9_live_store_map", None)
        if not callable(validator):
            raise ValidationError("Stage 9 live transport store binding is invalid")
        return validator(store_map)

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> object:
        if self.response is not None:
            raise ValidationError("Stage 9 provider transport may be called only once")
        response = self._delegate.get(
            path=path,
            query=query,
            headers=headers,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
        )
        self.response = response
        return response


@dataclass(frozen=True, slots=True)
class _TargetLayout:
    root: Path
    stores: Path
    backup: Path
    restored: Path
    rehearsal: Path
    receipts: Path


@dataclass(frozen=True, slots=True)
class _FmpBindings:
    request_factory: Callable[[BackfillScope], object]
    transport_factory: Callable[[str, StoreMap], object]
    importer_factory: Callable[[StoreMap, object], object]
    prepare: Callable[[object, object], object]
    publish: Callable[[object, object], object]
    provenance: Callable[[object, object], CandidateProvenance]
    correction_rehearsal: Callable[[object, StoreMap, Registry], None]


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        prog="python3 -m quant_data.operations.manual_backfill",
        add_help=False,
        description="Run only the approved nonproduction FMP SPY July 2026 backfill",
    )
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--target-root", required=True)
    return parser


def _approved_path(value: str | Path, field_name: str) -> tuple[Path, Path]:
    try:
        lexical = Path(value)
        if not lexical.is_absolute():
            raise ValueError
        resolved = lexical.resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise _ConfigurationFailure from exc
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve(strict=False):
        raise _ConfigurationFailure
    return lexical, resolved


def _require_exact_path(
    value: object,
    *,
    expected: str | Path,
    field_name: str,
) -> Path:
    expected_lexical, expected_resolved = _approved_path(expected, field_name)
    if not isinstance(value, str) or value != str(expected_lexical):
        raise _ArgumentFailure
    try:
        supplied = Path(value)
        if not supplied.is_absolute() or supplied.is_symlink():
            raise ValueError
        resolved = supplied.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise _ArgumentFailure from exc
    if resolved != expected_resolved:
        raise _ArgumentFailure
    return expected_resolved


def _preflight_project_root(value: object, expected: str | Path) -> Path:
    root = _require_exact_path(value, expected=expected, field_name="project_root")
    try:
        if not root.is_dir() or root.is_symlink():
            raise _ArgumentFailure
    except OSError:
        raise
    return root


def _preflight_target_root(value: object, expected: str | Path) -> Path:
    root = _require_exact_path(value, expected=expected, field_name="target_root")
    try:
        if root.is_symlink():
            raise _ArgumentFailure
        if root.exists():
            if not root.is_dir():
                raise _ArgumentFailure
            if next(root.iterdir(), None) is not None:
                raise ConflictError("Approved Stage 9 target is not empty")
    except OSError:
        raise
    return root


def _read_api_key(environment: Mapping[str, str]) -> str:
    value = environment.get("FMP_API_KEY")
    if not isinstance(value, str) or not value.strip():
        raise _ConfigurationFailure
    return value


def _registry_preflight(registry: Registry) -> None:
    """Reject any drift in the reviewed live FMP declaration before mkdir."""

    try:
        collectors = tuple(registry.collectors)
        collector_matches = [
            item for item in collectors if item.get("id") == FMP_COLLECTOR_ID
        ]
        migrations = tuple(registry.migrations)
        migration_matches = [
            item for item in migrations if item.id == FMP_MIGRATION_ID
        ]
        dataset_ids = {item.id for item in registry.datasets}
    except (AttributeError, TypeError) as exc:
        raise _ConfigurationFailure from exc
    if len(collector_matches) != 1 or len(migration_matches) != 1:
        raise _ConfigurationFailure
    collector = collector_matches[0]
    migration = migration_matches[0]
    if (
        collector.get("handler") != "market.fmp_daily_price"
        or collector.get("network") is not True
        or tuple(collector.get("configuration_env", ())) != ("FMP_API_KEY",)
        or tuple(collector.get("output_datasets", ()))
        != (
            FMP_EVIDENCE_DATASET_ID,
            FMP_IDENTITY_DATASET_ID,
            FMP_CANONICAL_DATASET_ID,
        )
        or migration.store != "market"
        or not isinstance(migration.sha256, str)
        or _SHA256.fullmatch(migration.sha256) is None
        or not {
            FMP_EVIDENCE_DATASET_ID,
            FMP_IDENTITY_DATASET_ID,
            FMP_CANONICAL_DATASET_ID,
        }.issubset(dataset_ids)
    ):
        raise _ConfigurationFailure


def _store_map_for(stores_root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=stores_root / "market.sqlite",
        macro=stores_root / "macro.sqlite",
        company=stores_root / "company.sqlite",
        news=stores_root / "news.sqlite",
    )


def _create_target_layout(root: Path) -> _TargetLayout:
    """Create only reviewed children after all read-only preflight succeeds."""

    try:
        if root.exists():
            if not root.is_dir() or root.is_symlink() or next(root.iterdir(), None) is not None:
                raise ConflictError("Approved Stage 9 target is not empty")
        else:
            root.mkdir(mode=0o700, parents=True, exist_ok=False)
        children = {
            "stores": root / "stores",
            "backup": root / "backup",
            "restored": root / "restored",
            "rehearsal": root / "rehearsal",
            "receipts": root / "receipts",
        }
        for child in children.values():
            child.mkdir(mode=0o700, exist_ok=False)
    except FileExistsError as exc:
        raise ConflictError("Approved Stage 9 target cannot be safely initialized") from exc
    return _TargetLayout(root=root, **children)


def _outcome(value: object) -> str:
    if isinstance(value, Mapping):
        outcome = value.get("outcome")
    else:
        outcome = getattr(value, "outcome", None)
    if not isinstance(outcome, str):
        raise ValidationError("Stage 9 importer publication result is invalid")
    return outcome


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValidationError(field_name + " must be a SHA-256 digest")
    return value


def _normalised_request_scope(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValidationError("Stage 9 candidate request scope is invalid")
    scope = dict(value)
    expected = BackfillScope().request_scope()
    allowed = set(expected)
    if set(scope) != allowed or scope != expected:
        raise ValidationError("Stage 9 candidate request scope drifted")
    return scope


def _default_candidate_provenance(candidate: object) -> CandidateProvenance:
    if isinstance(candidate, Mapping):
        request_scope = candidate.get("request_scope")
        response_sha256 = candidate.get("response_sha256")
        semantic_identity = candidate.get("semantic_identity")
        response = candidate.get("response")
    else:
        request_scope = getattr(candidate, "request_scope", None)
        response_sha256 = getattr(candidate, "response_sha256", None)
        semantic_identity = getattr(candidate, "semantic_identity", None)
        response = getattr(candidate, "response", None)
    if response is None:
        raise ValidationError("Stage 9 primary response is unavailable for rehearsal")
    return CandidateProvenance(
        request_scope=_normalised_request_scope(request_scope),
        response_sha256=_sha256(response_sha256, "response_sha256"),
        semantic_identity=_sha256(semantic_identity, "semantic_identity"),
        response=response,
    )


def _validated_provenance(value: CandidateProvenance) -> CandidateProvenance:
    if value.response is None:
        raise ValidationError("Stage 9 primary response is unavailable for rehearsal")
    return CandidateProvenance(
        request_scope=_normalised_request_scope(value.request_scope),
        response_sha256=_sha256(value.response_sha256, "response_sha256"),
        semantic_identity=_sha256(value.semantic_identity, "semantic_identity"),
        response=value.response,
    )


def _corrected_response_body(response: object) -> bytes:
    """Make one bounded OHLC-valid scratch-only correction from a saved body."""

    raw_body = getattr(response, "body", None)
    if not isinstance(raw_body, bytes):
        raise ValidationError("Stage 9 primary response is unavailable for rehearsal")
    try:
        value = loads_strict(raw_body.decode("utf-8"))
    except (UnicodeError, ValidationError, ResourceLimitError) as exc:
        raise ValidationError("Stage 9 primary response is invalid for rehearsal") from exc
    if not isinstance(value, list) or not value or not isinstance(value[0], dict):
        raise ValidationError("Stage 9 primary response is invalid for rehearsal")
    row = value[0]
    try:
        close = Decimal(row["close"])
        low = Decimal(row["low"])
        high = Decimal(row["high"])
    except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
        raise ValidationError("Stage 9 primary response is invalid for rehearsal") from exc
    increment = Decimal("0.01")
    corrected = close + increment if close + increment <= high else close - increment
    if corrected <= 0 or corrected == close or corrected < low or corrected > high:
        raise ValidationError("Stage 9 scratch correction cannot preserve OHLC bounds")
    row["close"] = corrected
    return dumps_strict(value).encode("utf-8")


def _default_fmp_bindings(registry: Registry) -> _FmpBindings:
    """Resolve the live adapter lazily, after all path/key/registry preflight."""

    try:
        from ..market.fmp_daily_prices import (
            CapturedFmpResponse,
            FmpDailyPriceImporter,
            FmpDailyPriceRequest,
            StdlibFmpDailyPriceTransport,
        )
    except ImportError as exc:
        raise CapabilityUnavailableError(
            "Stage 9 FMP adapter is unavailable",
            capability_id="stage9_fmp_daily_price",
        ) from exc

    prepared_contexts: dict[int, _LiveTransportContext] = {}

    def request_factory(scope: BackfillScope) -> object:
        return FmpDailyPriceRequest(
            symbol=scope.symbol,
            start_date=scope.start_date,
            end_date=scope.end_date,
            price_variant=scope.price_variant,
            currency_segment=scope.currency_segment,
        )

    def transport_factory(api_key: str, store_map: StoreMap) -> object:
        return _LiveTransportContext(
            api_key=api_key,
            transport=_ResponseTeeTransport(StdlibFmpDailyPriceTransport(store_map)),
        )

    def importer_factory(store_map: StoreMap, transport: object) -> object:
        if not isinstance(transport, _LiveTransportContext):
            raise ValidationError("Stage 9 FMP transport context is invalid")
        return _LiveImporterContext(
            importer=FmpDailyPriceImporter(store_map, registry),
            transport_context=transport,
        )

    def prepare(importer: object, request: object) -> object:
        if not isinstance(importer, _LiveImporterContext):
            raise ValidationError("Stage 9 FMP importer context is invalid")
        candidate = importer.importer.prepare(
            request,
            api_key=importer.transport_context.api_key,
            transport=importer.transport_context.transport,
        )
        response = getattr(importer.transport_context.transport, "response", None)
        if response is None:
            raise ValidationError("Stage 9 provider transport returned no response")
        importer.transport_context.response = response
        prepared_contexts[id(candidate)] = importer.transport_context
        return candidate

    def publish(importer: object, candidate: object) -> object:
        if not isinstance(importer, _LiveImporterContext):
            raise ValidationError("Stage 9 FMP importer context is invalid")
        return importer.importer.publish_prepared(candidate)

    def provenance(candidate: object, reconciliation: object) -> CandidateProvenance:
        context = prepared_contexts.get(id(candidate))
        if context is None or context.response is None:
            raise ValidationError("Stage 9 primary response is unavailable for rehearsal")
        return CandidateProvenance(
            request_scope=BackfillScope().request_scope(),
            response_sha256=_sha256(
                getattr(reconciliation, "response_sha256", None),
                "response_sha256",
            ),
            semantic_identity=_sha256(
                getattr(reconciliation, "semantic_identity", None),
                "semantic_identity",
            ),
            response=context.response,
        )

    def correction_rehearsal(
        response: object,
        rehearsal_map: StoreMap,
        rehearsal_registry: Registry,
    ) -> None:
        context = next(
            (
                item
                for item in prepared_contexts.values()
                if item.response is response
            ),
            None,
        )
        if context is None or not isinstance(response, CapturedFmpResponse):
            raise ValidationError("Stage 9 rehearsal response binding is invalid")
        corrected_response = CapturedFmpResponse(
            status=response.status,
            content_type=response.content_type,
            body=_corrected_response_body(response),
        )

        class OneResponseTransport:
            def __init__(self) -> None:
                self._used = False

            def get(self, **_kwargs: object) -> CapturedFmpResponse:
                if self._used:
                    raise ValidationError(
                        "Stage 9 rehearsal transport may be called only once"
                    )
                self._used = True
                return corrected_response

        rehearsal_importer = FmpDailyPriceImporter(rehearsal_map, rehearsal_registry)
        rehearsal_candidate = rehearsal_importer.prepare(
            FmpDailyPriceRequest(),
            api_key=context.api_key,
            transport=OneResponseTransport(),
        )
        if rehearsal_importer.publish_prepared(rehearsal_candidate).outcome != "succeeded":
            raise ValidationError("Stage 9 rehearsal correction did not succeed")
        with read_connection(
            rehearsal_map,
            StoreRole.MARKET,
            expected_anchor=rehearsal_registry.store(
                StoreRole.MARKET.value
            ).anchor_relation,
        ) as connection:
            counts = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM fmp_daily_price_captures),
                    (SELECT COUNT(*) FROM fmp_daily_price_versions),
                    (SELECT MAX(correction_sequence) FROM fmp_daily_price_versions)
                """
            ).fetchone()
            corrected_rows = tuple(
                connection.execute(
                    """
                    SELECT version.trade_date, version.close_value
                    FROM fmp_daily_price_versions AS version
                    JOIN fmp_daily_prices AS current
                      ON current.current_version_id=version.version_id
                    WHERE version.correction_sequence=2
                    ORDER BY version.trade_date
                    """
                )
            )
        if (
            counts is None
            or tuple(counts) != (2, 23, 2)
            or len(corrected_rows) != 1
        ):
            raise ValidationError(
                "Stage 9 rehearsal did not prove one correction version"
            )
        corrected_trade_date = corrected_rows[0]["trade_date"]
        corrected_close = corrected_rows[0]["close_value"]
        if not isinstance(corrected_trade_date, str) or not isinstance(corrected_close, str):
            raise ValidationError("Stage 9 rehearsal correction evidence is invalid")
        return None

    return _FmpBindings(
        request_factory=request_factory,
        transport_factory=transport_factory,
        importer_factory=importer_factory,
        prepare=prepare,
        publish=publish,
        provenance=provenance,
        correction_rehearsal=correction_rehearsal,
    )

def _resolve_bindings(
    *,
    registry: Registry,
    request_factory: Callable[[BackfillScope], object] | None,
    transport_factory: Callable[[str, StoreMap], object] | None,
    importer_factory: Callable[[StoreMap, object], object] | None,
    prepare: Callable[[object, object], object] | None,
    publish: Callable[[object, object], object] | None,
    provenance: Callable[[object, object], CandidateProvenance] | None,
    correction_rehearsal: Callable[[object, StoreMap, Registry], None] | None,
) -> _FmpBindings:
    supplied = (
        request_factory,
        transport_factory,
        importer_factory,
        prepare,
        publish,
        provenance,
        correction_rehearsal,
    )
    defaults = _default_fmp_bindings(registry) if any(item is None for item in supplied) else None
    return _FmpBindings(
        request_factory=request_factory or defaults.request_factory,  # type: ignore[union-attr]
        transport_factory=transport_factory or defaults.transport_factory,  # type: ignore[union-attr]
        importer_factory=importer_factory or defaults.importer_factory,  # type: ignore[union-attr]
        prepare=prepare or defaults.prepare,  # type: ignore[union-attr]
        publish=publish or defaults.publish,  # type: ignore[union-attr]
        provenance=provenance or defaults.provenance,  # type: ignore[union-attr]
        correction_rehearsal=correction_rehearsal or defaults.correction_rehearsal,  # type: ignore[union-attr]
    )


def _digest_field(value: object, field_name: str) -> str:
    if isinstance(value, Mapping):
        candidate = value.get(field_name)
    else:
        candidate = getattr(value, field_name, None)
    return _sha256(candidate, field_name)


def _success_payload(evidence: object, receipt: object) -> dict[str, object]:
    return {
        "contract": "quant_data.stage9_manual_backfill_result",
        "contract_version": "1.0.0",
        "provider": FMP_PROVIDER,
        "symbol": FMP_SYMBOL,
        "start_date": FMP_REQUEST_START_DATE,
        "end_date": FMP_REQUEST_END_DATE,
        "publication_outcome": "succeeded",
        "replay_outcome": "unchanged",
        "correction_rehearsed": True,
        "promotion_state": "candidate_only_no_operational_promotion",
        "evidence_sha256": _digest_field(evidence, "sha256"),
        "receipt_sha256": _digest_field(receipt, "receipt_sha256"),
    }


def _write_success(stream: TextIO, evidence: object, receipt: object) -> None:
    stream.write(dumps_strict(_success_payload(evidence, receipt)))
    stream.write("\n")
    stream.flush()


def _write_error(stream: TextIO, code: str, exit_code: int) -> None:
    stream.write(
        dumps_strict(
            {
                "contract": "quant_data.stage9_manual_backfill_error",
                "contract_version": "1.0.0",
                "error": code,
                "exit_code": exit_code,
            }
        )
    )
    stream.write("\n")
    stream.flush()


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


def _default_code_revision(registry: Registry) -> str:
    return _sha256(getattr(registry, "source_sha256", None), "registry_source_sha256")


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    environment: Mapping[str, str] | None = None,
    registry_loader: Callable[..., Registry] = load_registry,
    registry_validator: Callable[[Registry], None] = _registry_preflight,
    store_map_factory: Callable[[Path], StoreMap] = _store_map_for,
    initializer: Callable[[StoreMap, Registry], object] = initialize_all,
    request_factory: Callable[[BackfillScope], object] | None = None,
    transport_factory: Callable[[str, StoreMap], object] | None = None,
    importer_factory: Callable[[StoreMap, object], object] | None = None,
    prepare: Callable[[object, object], object] | None = None,
    publish: Callable[[object, object], object] | None = None,
    provenance: Callable[[object, object], CandidateProvenance] | None = None,
    reconcile: Callable[[StoreMap, Registry], object] = reconcile_fmp_spy_market,
    backup: Callable[..., object] = backup_all,
    restore: Callable[..., object] = restore_all,
    correction_rehearsal: Callable[[object, StoreMap, Registry], None] | None = None,
    evidence_builder: Callable[..., object] = build_repopulation_evidence,
    receipt_state_factory: Callable[[Path], object] = PrivatePromotionCandidateState,
    receipt_publisher: Callable[..., object] = publish_promotion_candidate,
    now: Callable[[], object] = _default_now,
    code_revision: Callable[[Registry], str] = _default_code_revision,
    approved_project_root: str | Path = APPROVED_PROJECT_ROOT,
    approved_target_root: str | Path = APPROVED_TARGET_ROOT,
) -> int:
    """Run the one reviewed nonproduction FMP SPY July-2026 workflow.

    Every dependency that could contact a provider or touch a store is injected.
    This keeps focused tests network-free and gives the live adapter one narrow
    binding point without exposing a generic command surface.
    """

    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    try:
        arguments = _parser().parse_args(argv)
        project_root = _preflight_project_root(
            arguments.project_root,
            approved_project_root,
        )
        target_root = _preflight_target_root(
            arguments.target_root,
            approved_target_root,
        )
        api_key = _read_api_key(environment if environment is not None else os.environ)
        try:
            registry = registry_loader(
                project_root / CANONICAL_REGISTRY_PATH,
                project_root=project_root,
                environment={},
            )
            if (
                getattr(registry, "schema_version", None) == "1.6.0"
                and getattr(registry, "registry_version", None) == "2.8.0"
            ):
                registry = stage9_registry_profile(registry)
            registry_validator(registry)
        except RegistryError:
            raise
        except (AttributeError, TypeError, ValidationError) as exc:
            raise _ConfigurationFailure from exc
        bindings = _resolve_bindings(
            registry=registry,
            request_factory=request_factory,
            transport_factory=transport_factory,
            importer_factory=importer_factory,
            prepare=prepare,
            publish=publish,
            provenance=provenance,
            correction_rehearsal=correction_rehearsal,
        )

        store_map = store_map_factory(target_root / "stores")
        scope = BackfillScope()
        request = bindings.request_factory(scope)
        transport = bindings.transport_factory(api_key, store_map)
        importer = bindings.importer_factory(store_map, transport)
        candidate = bindings.prepare(importer, request)

        layout = _create_target_layout(target_root)
        if layout.stores != target_root / "stores":
            raise ValidationError("Stage 9 target layout drifted")
        initializer(store_map, registry)
        publication = bindings.publish(importer, candidate)
        if _outcome(publication) != "succeeded":
            raise ValidationError("Stage 9 primary publication did not succeed")
        reconciliation = reconcile(store_map, registry)
        candidate_provenance = bindings.provenance(candidate, reconciliation)
        if not isinstance(candidate_provenance, CandidateProvenance):
            raise ValidationError("Stage 9 candidate provenance is invalid")
        candidate_provenance = _validated_provenance(candidate_provenance)
        replay = bindings.publish(importer, candidate)
        if _outcome(replay) != "unchanged":
            raise ValidationError("Stage 9 replay was not unchanged")

        backup_cohort = backup(store_map, registry, target_root=layout.backup)
        restored_cohort = restore(
            backup_cohort,
            registry,
            target_root=layout.restored,
        )
        rehearsal_cohort = restore(
            backup_cohort,
            registry,
            target_root=layout.rehearsal,
        )
        rehearsal_map = getattr(rehearsal_cohort, "store_map", None)
        if not isinstance(rehearsal_map, StoreMap):
            raise ValidationError("Stage 9 rehearsal restore cohort is invalid")
        correction_result = bindings.correction_rehearsal(
            candidate_provenance.response,
            rehearsal_map,
            registry,
        )
        if correction_result is not None:
            raise ValidationError("Stage 9 correction rehearsal must not supply evidence")
        restored_map = getattr(restored_cohort, "store_map", None)
        if not isinstance(restored_map, StoreMap):
            raise ValidationError("Stage 9 restored cohort is invalid")
        evidence = evidence_builder(
            store_map,
            restored_map,
            registry,
            request_scope=candidate_provenance.request_scope,
            response_sha256=candidate_provenance.response_sha256,
            semantic_identity=candidate_provenance.semantic_identity,
            rehearsal_map=rehearsal_map,
            replay_unchanged=True,
        )
        receipt_state = receipt_state_factory(layout.receipts)
        receipt = receipt_publisher(
            receipt_state,
            evidence,
            code_revision=code_revision(registry),
            now=now,
            attempt_id=_ATTEMPT_ID,
        )
        _write_success(output, evidence, receipt)
        return 0
    except _ArgumentFailure:
        exit_code, code = 64, "invalid_arguments"
    except _ConfigurationFailure:
        exit_code, code = 78, "invalid_configuration"
    except (CapabilityUnavailableError, StoreUnavailableError):
        exit_code, code = 69, "unavailable"
    except OSError:
        exit_code, code = 74, "local_io"
    except ConflictError:
        exit_code, code = 75, "conflict"
    except RegistryError:
        exit_code, code = 78, "invalid_configuration"
    except (ValidationError, MigrationError, ResourceLimitError):
        exit_code, code = 70, "contract_failure"
    except Exception:
        exit_code, code = 70, "internal_failure"
    _write_error(errors, code, exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
