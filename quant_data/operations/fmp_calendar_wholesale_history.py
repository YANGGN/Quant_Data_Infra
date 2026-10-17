"""Manual, resumable wholesale FMP US calendar evidence history operation.

The completed GDP/CPI history is deliberately not used as a checkpoint here:
that normalized material did not retain the whole FMP payload.  This operation
instead checkpoints each fixed request window in the new private wholesale
evidence relation.  A safely stored raw response is replayed locally on a
continuation, so a parser or downstream employment-normalization failure never
requires a second provider request for that window.

The operation remains manual-only.  It reuses the reviewed one-attempt FMP
transport boundary, has no scheduler and makes no GDP/CPI normalized writes.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
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
    FMP_ECONOMIC_CALENDAR_URL,
    FMP_MACRO_CALENDAR_WINDOWS,
    MACRO_STORE,
    PROJECT_ROOT,
    _JSON_MEDIA_TYPE,
    _MAX_RESPONSE_BYTES,
    _StdlibTransport,
    _TIMEOUT_SECONDS,
    _USER_AGENT,
    _utc_text,
    _validate_response,
    FmpMacroCalendarTransport,
    FmpMacroCalendarTransportResponse,
    FmpMacroCalendarWindow,
)


FMP_US_CALENDAR_WHOLESALE_WINDOWS: Final = FMP_MACRO_CALENDAR_WINDOWS
_VERSION: Final = "1.0.0"


class _WholesalePublisher(Protocol):
    def completed_windows(self) -> tuple[tuple[str, str], ...]: ...

    def load_latest_window(self, start_date: str, end_date: str) -> object | None: ...

    def publish(self, capture: object) -> object: ...


ParseFmpWholesaleCalendar = Callable[..., object]
WholesalePublisherFactory = Callable[[], _WholesalePublisher]
EmploymentParser = Callable[..., object]
EmploymentPublisherFactory = Callable[[], object]
CredentialReader = Callable[..., str]


@dataclass(frozen=True, slots=True)
class FmpWholesaleCalendarHistoryReport:
    """Safe, aggregate outcome for one fixed wholesale evidence run."""

    requested: int
    published: int
    unchanged: int
    written_rows: int
    replayed_windows: int
    employment_published: int
    employment_unchanged: int
    employment_written_versions: int
    employment_normalization_pending: int
    employment_recognized_events: int

    def mapping(self) -> dict[str, object]:
        return {
            "requested": self.requested,
            "published": self.published,
            "unchanged": self.unchanged,
            "written_rows": self.written_rows,
            "replayed_windows": self.replayed_windows,
            "employment_published": self.employment_published,
            "employment_unchanged": self.employment_unchanged,
            "employment_written_versions": self.employment_written_versions,
            "employment_normalization_pending": self.employment_normalization_pending,
            "employment_recognized_events": self.employment_recognized_events,
        }


def _require_bound_target(
    *, project_root: Path, macro_store: Path, canonical: bool
) -> None:
    """Ensure fixture and canonical invocations cannot redirect the store."""

    try:
        root = project_root.resolve(strict=True)
        target = macro_store.resolve(strict=True)
        root_info = project_root.lstat()
        target_info = macro_store.lstat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError("FMP wholesale calendar target is unavailable") from exc
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
        raise ValidationError("FMP wholesale calendar target binding is invalid")
    if canonical:
        if project_root != PROJECT_ROOT or macro_store != MACRO_STORE:
            raise ValidationError("FMP wholesale calendar canonical target binding is invalid")
        return
    try:
        project_root.relative_to(Path("/tmp").resolve(strict=True))
    except ValueError as exc:
        raise ValidationError("FMP wholesale calendar fixtures must use a temporary root") from exc


def _completed_window_set(value: object) -> set[tuple[str, str]]:
    if (
        not isinstance(value, tuple)
        or any(
            not isinstance(item, tuple)
            or len(item) != 2
            or any(not isinstance(component, str) for component in item)
            for item in value
        )
    ):
        raise ConflictError("FMP wholesale completed-window state is invalid")
    result = set(value)
    if len(result) != len(value):
        raise ConflictError("FMP wholesale completed-window state is invalid")
    planned = {
        (window.start_date.isoformat(), window.end_date.isoformat())
        for window in FMP_US_CALENDAR_WHOLESALE_WINDOWS
    }
    if not result.issubset(planned):
        raise ConflictError("FMP wholesale completed windows are outside the fixed plan")
    return result


def _wholesale_publication_result(value: object) -> tuple[str, int]:
    outcome = getattr(value, "outcome", None)
    semantic_identity = getattr(value, "semantic_identity", None)
    capture_id = getattr(value, "capture_id", None)
    written_rows = getattr(value, "written_rows", None)
    written_captures = getattr(value, "written_captures", None)
    if (
        outcome not in {"published", "unchanged"}
        or not isinstance(semantic_identity, str)
        or not semantic_identity
        or len(semantic_identity) > 256
        or not isinstance(capture_id, str)
        or not capture_id
        or len(capture_id) > 256
        or isinstance(written_rows, bool)
        or not isinstance(written_rows, int)
        or written_rows < 0
        or isinstance(written_captures, bool)
        or not isinstance(written_captures, int)
        or written_captures < 0
        or (outcome == "unchanged") != (
            written_rows == 0 and written_captures == 0
        )
    ):
        raise ConflictError("FMP wholesale calendar publisher outcome is invalid")
    return outcome, written_rows


def _normalized_publication_result(value: object) -> tuple[str, int]:
    outcome = getattr(value, "outcome", None)
    semantic_identity = getattr(value, "semantic_identity", None)
    written_versions = getattr(value, "written_versions", None)
    if (
        outcome not in {"published", "unchanged"}
        or not isinstance(semantic_identity, str)
        or not semantic_identity
        or len(semantic_identity) > 256
        or isinstance(written_versions, bool)
        or not isinstance(written_versions, int)
        or written_versions < 0
        or (outcome == "unchanged") != (written_versions == 0)
    ):
        raise ConflictError("FMP employment calendar publisher outcome is invalid")
    return outcome, written_versions


def _stored_capture_payload(capture: object) -> tuple[bytes, str, str, str]:
    """Obtain only the fixed source fields required for local replay."""

    body = getattr(capture, "response_bytes", None)
    captured_at = getattr(capture, "captured_at", None)
    start_date = getattr(capture, "request_start_date", None)
    end_date = getattr(capture, "request_end_date", None)
    if (
        not isinstance(body, bytes)
        or not body
        or not isinstance(captured_at, str)
        or not captured_at
        or not isinstance(start_date, str)
        or not isinstance(end_date, str)
    ):
        raise ConflictError("FMP wholesale stored capture is invalid")
    return body, captured_at, start_date, end_date


class FmpWholesaleCalendarHistoryRunner:
    """Persist the fixed raw plan before optionally extracting employment facts."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        macro_store: str | Path,
        parser: ParseFmpWholesaleCalendar,
        publisher_factory: WholesalePublisherFactory,
        transport: FmpMacroCalendarTransport,
        credential_environment: Mapping[str, str],
        employment_parser: EmploymentParser | None = None,
        employment_publisher_factory: EmploymentPublisherFactory | None = None,
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
        self._employment_parser = employment_parser
        self._employment_publisher_factory = employment_publisher_factory
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
            or (self._employment_parser is None)
            != (self._employment_publisher_factory is None)
            or (
                self._employment_parser is not None
                and not callable(self._employment_parser)
            )
            or (
                self._employment_publisher_factory is not None
                and not callable(self._employment_publisher_factory)
            )
        ):
            raise ValidationError("FMP wholesale calendar runner dependencies are invalid")

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
        self, window: FmpMacroCalendarWindow, api_key: str
    ) -> FmpMacroCalendarTransportResponse:
        response = self._transport.request(
            url=FMP_ECONOMIC_CALENDAR_URL,
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

    def _normalize_employment(
        self,
        *,
        capture: object,
        employment_publisher: object | None,
    ) -> tuple[str | None, int, int]:
        """Return employment outcome, versions, and recognized events.

        An unsupported or absent reviewed employment alias is a diagnostic, not
        a raw-evidence failure.  Publisher failures remain visible and are
        resumable from the wholesale capture on the next run.
        """

        if self._employment_parser is None or employment_publisher is None:
            return None, 0, 0
        body, captured_at, start_date, end_date = _stored_capture_payload(capture)
        try:
            normalized = self._employment_parser(
                body,
                captured_at=captured_at,
                start_date=start_date,
                end_date=end_date,
            )
        except ValidationError:
            return "pending", 0, 0
        events = getattr(normalized, "events", None)
        if not isinstance(events, tuple) or not events:
            raise ConflictError("FMP employment parser result is invalid")
        if not callable(getattr(employment_publisher, "publish", None)):
            raise ConflictError("FMP employment calendar publisher is invalid")
        outcome, written_versions = _normalized_publication_result(
            employment_publisher.publish(normalized)
        )
        return outcome, written_versions, len(events)

    def run(self) -> FmpWholesaleCalendarHistoryReport:
        publisher = self._publisher_factory()
        if (
            not callable(getattr(publisher, "publish", None))
            or not callable(getattr(publisher, "completed_windows", None))
            or not callable(getattr(publisher, "load_latest_window", None))
        ):
            raise ConflictError("FMP wholesale calendar publisher is invalid")
        completed = _completed_window_set(publisher.completed_windows())
        employment_publisher = (
            self._employment_publisher_factory()
            if self._employment_publisher_factory is not None
            else None
        )
        api_key: str | None = None
        requested = 0
        published = 0
        unchanged = 0
        written_rows = 0
        replayed_windows = 0
        employment_published = 0
        employment_unchanged = 0
        employment_written_versions = 0
        employment_normalization_pending = 0
        employment_recognized_events = 0

        for window in FMP_US_CALENDAR_WHOLESALE_WINDOWS:
            start_date = window.start_date.isoformat()
            end_date = window.end_date.isoformat()
            key = (start_date, end_date)
            if key in completed:
                capture = publisher.load_latest_window(start_date, end_date)
                if capture is None:
                    raise ConflictError("FMP wholesale completed capture is unavailable")
                replayed_windows += 1
            else:
                if api_key is None:
                    api_key = self._read_api_key_once()
                response = self._request(window, api_key)
                requested += 1
                capture = self._parser(
                    response.body,
                    captured_at=_utc_text(self._utcnow()),
                    start_date=start_date,
                    end_date=end_date,
                )
                outcome, rows = _wholesale_publication_result(publisher.publish(capture))
                published += int(outcome == "published")
                unchanged += int(outcome == "unchanged")
                written_rows += rows

            employment_outcome, versions, recognized = self._normalize_employment(
                capture=capture,
                employment_publisher=employment_publisher,
            )
            employment_published += int(employment_outcome == "published")
            employment_unchanged += int(employment_outcome == "unchanged")
            employment_normalization_pending += int(employment_outcome == "pending")
            employment_written_versions += versions
            employment_recognized_events += recognized

        return FmpWholesaleCalendarHistoryReport(
            requested=requested,
            published=published,
            unchanged=unchanged,
            written_rows=written_rows,
            replayed_windows=replayed_windows,
            employment_published=employment_published,
            employment_unchanged=employment_unchanged,
            employment_written_versions=employment_written_versions,
            employment_normalization_pending=employment_normalization_pending,
            employment_recognized_events=employment_recognized_events,
        )


def _require_canonical_target() -> None:
    _require_bound_target(
        project_root=PROJECT_ROOT,
        macro_store=MACRO_STORE,
        canonical=True,
    )



def _domain_api() -> tuple[
    ParseFmpWholesaleCalendar,
    WholesalePublisherFactory,
    EmploymentParser,
    EmploymentPublisherFactory,
]:
    """Load canonical write dependencies only after the target binding check."""

    _require_canonical_target()
    from ..macro.fmp_calendar_wholesale import (
        FmpWholesaleCalendarPublisher,
        parse_fmp_us_calendar_wholesale,
    )
    from ..macro.fmp_release_surprises import (
        FmpEmploymentCalendarPublisher,
        parse_fmp_us_employment_calendar,
    )

    registry = load_registry(
        PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )

    def wholesale_publisher_factory() -> _WholesalePublisher:
        return FmpWholesaleCalendarPublisher.for_canonical(registry=registry)

    def employment_publisher_factory() -> object:
        return FmpEmploymentCalendarPublisher.for_canonical(registry=registry)

    return (
        parse_fmp_us_calendar_wholesale,
        wholesale_publisher_factory,
        parse_fmp_us_employment_calendar,
        employment_publisher_factory,
    )


def populate_fmp_us_calendar_wholesale_history_live() -> FmpWholesaleCalendarHistoryReport:
    """Run the approved one-time wholesale US calendar evidence plan."""

    parser, publisher_factory, employment_parser, employment_publisher_factory = (
        _domain_api()
    )
    return FmpWholesaleCalendarHistoryRunner(
        project_root=PROJECT_ROOT,
        macro_store=MACRO_STORE,
        parser=parser,
        publisher_factory=publisher_factory,
        employment_parser=employment_parser,
        employment_publisher_factory=employment_publisher_factory,
        transport=_StdlibTransport(),
        credential_environment=os.environ,
        _canonical=True,
    ).run()


class _ArgumentFailure(Exception):
    """Sanitized CLI argument rejection."""


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _ArgumentFailure


def main(argv: list[str] | None = None) -> int:
    try:
        _SafeArgumentParser(add_help=False).parse_args(argv)
        report = populate_fmp_us_calendar_wholesale_history_live()
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
                "contract": "quant_data.fmp_calendar_wholesale_history_error",
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
    "FMP_US_CALENDAR_WHOLESALE_WINDOWS",
    "FmpWholesaleCalendarHistoryReport",
    "FmpWholesaleCalendarHistoryRunner",
    "populate_fmp_us_calendar_wholesale_history_live",
)
