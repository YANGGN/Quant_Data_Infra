"""Manual-only, bounded FMP Treasury yield-curve history operation.

This module owns the provider boundary for exactly one caller-supplied,
inclusive Treasury curve window. It validates and parses the entire response
before calling the publisher, so provider work never occurs while the macro
writer can hold its SQLite lock. It creates no scheduler and does not invoke
the provider at import time.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import stat
import sys
from typing import Final, Protocol

from ..credentials import read_project_credential
from ..errors import (
    ConflictError,
    RegistryError,
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from ..json_codec import dumps_strict
from ..registry import CANONICAL_REGISTRY_PATH, load_registry
from .fmp_macro_calendar_history import (
    MACRO_STORE,
    PROJECT_ROOT,
    FmpMacroCalendarTransport,
    FmpMacroCalendarTransportResponse,
    _JSON_MEDIA_TYPE,
    _StdlibTransport,
    _utc_text,
)


FMP_TREASURY_RATES_URL: Final = "https://financialmodelingprep.com/stable/treasury-rates"
FMP_TREASURY_YIELD_CURVE_HISTORY_COLLECTOR_ID: Final = (
    "fmp.macro.treasury_yield_curve_history"
)
FMP_TREASURY_YIELD_CURVE_HISTORY_HANDLER: Final = (
    "macro.fmp_treasury_yield_curve_history"
)
MAX_WINDOW_DAYS: Final = 366
_TIMEOUT_SECONDS: Final = 60
_MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024
_USER_AGENT: Final = "QuantDataInfra/1.0"
_VERSION: Final = "1.0.0"


@dataclass(frozen=True, slots=True)
class FmpTreasuryCurveWindow:
    """One caller-supplied inclusive Treasury curve request window."""

    start_date: date
    end_date: date

    def __post_init__(self) -> None:
        if (
            not isinstance(self.start_date, date)
            or isinstance(self.start_date, datetime)
            or not isinstance(self.end_date, date)
            or isinstance(self.end_date, datetime)
            or self.start_date > self.end_date
            or (self.end_date - self.start_date).days + 1 > MAX_WINDOW_DAYS
        ):
            raise ValidationError("FMP Treasury curve window is invalid")

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "from": self.start_date.isoformat(),
            "to": self.end_date.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class FmpTreasuryCurveHistoryReport:
    """Credential- and path-free aggregate outcome for one request window."""

    requested: int
    published: int
    unchanged: int
    written_curves: int
    written_curve_versions: int
    written_observation_versions: int
    start_date: date
    end_date: date

    def mapping(self) -> dict[str, object]:
        return {
            "requested": self.requested,
            "published": self.published,
            "unchanged": self.unchanged,
            "written_curves": self.written_curves,
            "written_curve_versions": self.written_curve_versions,
            "written_observation_versions": self.written_observation_versions,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
        }


class _Publisher(Protocol):
    def publish(self, capture: object) -> object: ...


ParseFmpTreasuryCurve = Callable[..., object]
PublisherFactory = Callable[[], _Publisher]
CredentialReader = Callable[..., str]


def _media_type(value: object) -> str:
    if not isinstance(value, str) or len(value) > 256:
        raise StoreUnavailableError("FMP Treasury curve source media type is invalid")
    result = value.split(";", 1)[0].strip().casefold()
    if not result:
        raise StoreUnavailableError("FMP Treasury curve source media type is invalid")
    return result


def _validate_response(
    response: FmpMacroCalendarTransportResponse,
) -> FmpMacroCalendarTransportResponse:
    """Accept exactly one bounded, direct JSON provider response."""

    if not isinstance(response, FmpMacroCalendarTransportResponse):
        raise StoreUnavailableError("FMP Treasury curve transport returned an invalid response")
    if response.redirected:
        raise StoreUnavailableError("FMP Treasury curve provider redirect is not allowed")
    if isinstance(response.status, bool) or response.status != 200:
        raise StoreUnavailableError("FMP Treasury curve provider response is outside policy")
    if _media_type(response.media_type) != _JSON_MEDIA_TYPE:
        raise StoreUnavailableError("FMP Treasury curve provider media type is invalid")
    if not isinstance(response.body, bytes) or not response.body:
        raise StoreUnavailableError("FMP Treasury curve provider response body is invalid")
    if len(response.body) > _MAX_RESPONSE_BYTES:
        raise ResourceLimitError("FMP Treasury curve provider response exceeds its byte bound")
    try:
        json.loads(response.body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StoreUnavailableError("FMP Treasury curve provider JSON is invalid") from exc
    return response


def _require_bound_target(
    *, project_root: Path, macro_store: Path, canonical: bool
) -> None:
    """Ensure fixture and canonical callers cannot redirect the macro store."""

    try:
        root = project_root.resolve(strict=True)
        target = macro_store.resolve(strict=True)
        root_info = project_root.lstat()
        target_info = macro_store.lstat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError("FMP Treasury curve target is unavailable") from exc
    if (
        root != project_root
        or target != macro_store
        or project_root.is_symlink()
        or macro_store.is_symlink()
        or not stat.S_ISDIR(root_info.st_mode)
        or not stat.S_ISREG(target_info.st_mode)
        or target_info.st_nlink != 1
        or macro_store != project_root / "data" / "macro.sqlite"
    ):
        raise ValidationError("FMP Treasury curve target binding is invalid")
    if canonical:
        if project_root != PROJECT_ROOT or macro_store != MACRO_STORE:
            raise ValidationError("FMP Treasury curve canonical target binding is invalid")
        return
    try:
        project_root.relative_to(Path("/tmp").resolve(strict=True))
    except ValueError as exc:
        raise ValidationError("FMP Treasury curve fixtures must use a temporary root") from exc


def _as_date(value: date | str, *, name: str) -> date:
    if isinstance(value, datetime):
        raise ValidationError(f"{name} must be an ISO calendar date")
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be an ISO calendar date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{name} must be an ISO calendar date") from exc


def _publication_result(value: object) -> tuple[str, int, int, int]:
    """Validate the publisher's aggregate, safe result shape."""

    outcome = getattr(value, "outcome", None)
    semantic_identity = getattr(value, "semantic_identity", None)
    written_curves = getattr(value, "written_curves", None)
    written_curve_versions = getattr(value, "written_curve_versions", None)
    written_observation_versions = getattr(value, "written_observation_versions", None)
    if semantic_identity is not None and (
        not isinstance(semantic_identity, str)
        or not semantic_identity
        or len(semantic_identity) > 256
    ):
        raise ConflictError("FMP Treasury curve publisher outcome is invalid")
    if (
        outcome not in {"published", "unchanged"}
        or isinstance(written_curves, bool)
        or not isinstance(written_curves, int)
        or written_curves < 0
        or isinstance(written_curve_versions, bool)
        or not isinstance(written_curve_versions, int)
        or written_curve_versions < 0
        or isinstance(written_observation_versions, bool)
        or not isinstance(written_observation_versions, int)
        or written_observation_versions < 0
        or (
            outcome == "unchanged"
            and (
                written_curves != 0
                or written_curve_versions != 0
                or written_observation_versions != 0
            )
        )
    ):
        raise ConflictError("FMP Treasury curve publisher outcome is invalid")
    return (
        outcome,
        written_curves,
        written_curve_versions,
        written_observation_versions,
    )


class FmpTreasuryCurveHistoryRunner:
    """Prepare and publish one bounded Treasury curve response."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        macro_store: str | Path,
        parser: ParseFmpTreasuryCurve,
        publisher_factory: PublisherFactory,
        transport: FmpMacroCalendarTransport,
        credential_environment: Mapping[str, str],
        credential_reader: CredentialReader = read_project_credential,
        utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        _canonical: bool = False,
    ) -> None:
        self._project_root = Path(project_root)
        self._macro_store = Path(macro_store)
        self._parser = parser
        self._publisher_factory = publisher_factory
        self._transport = transport
        self._credential_environment = credential_environment
        self._credential_reader = credential_reader
        self._utcnow = utcnow
        _require_bound_target(
            project_root=self._project_root,
            macro_store=self._macro_store,
            canonical=_canonical,
        )
        if (
            not callable(self._parser)
            or not callable(self._publisher_factory)
            or not callable(getattr(self._transport, "request", None))
            or not isinstance(self._credential_environment, Mapping)
            or not callable(self._credential_reader)
            or not callable(self._utcnow)
        ):
            raise ValidationError("FMP Treasury curve runner dependencies are invalid")

    def _read_api_key_once(self) -> str:
        try:
            value = self._credential_reader(
                project_root=self._project_root,
                name="FMP_API_KEY",
                environment=self._credential_environment,
            )
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError("Credential is missing or invalid") from exc
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 4096
            or any(
                character.isspace()
                or ord(character) < 32
                or ord(character) == 127
                for character in value
            )
        ):
            raise ValidationError("Credential is missing or invalid")
        return value

    def _request(
        self, window: FmpTreasuryCurveWindow, api_key: str
    ) -> FmpMacroCalendarTransportResponse:
        response = self._transport.request(
            url=FMP_TREASURY_RATES_URL,
            parameters=window.parameters,
            headers={
                "Accept": _JSON_MEDIA_TYPE,
                "User-Agent": _USER_AGENT,
                "apikey": api_key,
            },
            timeout_seconds=_TIMEOUT_SECONDS,
            max_bytes=_MAX_RESPONSE_BYTES,
        )
        return _validate_response(response)

    def run(
        self,
        *,
        start_date: date | str,
        end_date: date | str,
    ) -> FmpTreasuryCurveHistoryReport:
        """Issue exactly one inclusive request and publish its prepared facts."""

        window = FmpTreasuryCurveWindow(
            start_date=_as_date(start_date, name="start_date"),
            end_date=_as_date(end_date, name="end_date"),
        )
        publisher = self._publisher_factory()
        if not callable(getattr(publisher, "publish", None)):
            raise ConflictError("FMP Treasury curve publisher is invalid")

        api_key = self._read_api_key_once()
        response = self._request(window, api_key)
        capture = self._parser(
            response.body,
            captured_at=_utc_text(self._utcnow()),
            start_date=window.start_date.isoformat(),
            end_date=window.end_date.isoformat(),
        )
        (
            outcome,
            written_curves,
            written_curve_versions,
            written_observation_versions,
        ) = _publication_result(
            publisher.publish(capture)
        )
        return FmpTreasuryCurveHistoryReport(
            requested=1,
            published=int(outcome == "published"),
            unchanged=int(outcome == "unchanged"),
            written_curves=written_curves,
            written_curve_versions=written_curve_versions,
            written_observation_versions=written_observation_versions,
            start_date=window.start_date,
            end_date=window.end_date,
        )


def _require_canonical_target() -> None:
    _require_bound_target(
        project_root=PROJECT_ROOT,
        macro_store=MACRO_STORE,
        canonical=True,
    )


def _domain_api() -> tuple[ParseFmpTreasuryCurve, PublisherFactory]:
    """Bind the reviewed parser and publisher to the canonical macro store."""

    from ..macro.fmp_treasury_curve import (
        FmpTreasuryCurvePublisher,
        parse_fmp_treasury_curve,
    )

    registry = load_registry(
        CANONICAL_REGISTRY_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )

    def publisher_factory() -> FmpTreasuryCurvePublisher:
        return FmpTreasuryCurvePublisher(
            project_root=PROJECT_ROOT,
            macro_store=MACRO_STORE,
            registry=registry,
            _canonical=True,
        )

    return parse_fmp_treasury_curve, publisher_factory


def populate_fmp_treasury_curve_history_live(
    *, start_date: date | str, end_date: date | str
) -> FmpTreasuryCurveHistoryReport:
    """Run one explicit canonical Treasury curve history window."""

    parser, publisher_factory = _domain_api()
    return FmpTreasuryCurveHistoryRunner(
        project_root=PROJECT_ROOT,
        macro_store=MACRO_STORE,
        parser=parser,
        publisher_factory=publisher_factory,
        transport=_StdlibTransport(),
        credential_environment=os.environ,
        _canonical=True,
    ).run(start_date=start_date, end_date=end_date)


class _ArgumentFailure(Exception):
    """Sanitized CLI argument rejection."""


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _ArgumentFailure


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(add_help=False)
    parser.add_argument("--from", dest="start_date", required=True)
    parser.add_argument("--to", dest="end_date", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run a manually supplied window with credential-free diagnostics."""

    try:
        arguments = _parser().parse_args(argv)
        report = populate_fmp_treasury_curve_history_live(
            start_date=arguments.start_date,
            end_date=arguments.end_date,
        )
    except (_ArgumentFailure, ValidationError):
        code, name = 64, "invalid_request"
    except StoreUnavailableError:
        code, name = 69, "store_unavailable"
    except (RegistryError, ConflictError):
        code, name = 75, "temporary_conflict"
    except ResourceLimitError:
        code, name = 74, "local_io"
    except Exception:
        code, name = 70, "internal_failure"
    else:
        sys.stdout.write(dumps_strict(report.mapping()) + "\n")
        sys.stdout.flush()
        return 0
    sys.stderr.write(
        dumps_strict(
            {
                "contract": "quant_data.fmp_treasury_curve_history_error",
                "error": name,
                "exit_code": code,
                "version": _VERSION,
            }
        )
        + "\n"
    )
    sys.stderr.flush()
    return code


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())


__all__ = (
    "FMP_TREASURY_RATES_URL",
    "FMP_TREASURY_YIELD_CURVE_HISTORY_COLLECTOR_ID",
    "FMP_TREASURY_YIELD_CURVE_HISTORY_HANDLER",
    "MACRO_STORE",
    "MAX_WINDOW_DAYS",
    "PROJECT_ROOT",
    "FmpTreasuryCurveHistoryReport",
    "FmpTreasuryCurveHistoryRunner",
    "FmpTreasuryCurveWindow",
    "populate_fmp_treasury_curve_history_live",
)
