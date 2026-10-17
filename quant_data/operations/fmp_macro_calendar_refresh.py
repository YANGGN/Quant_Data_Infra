"""Scheduled one-request refresh for current FMP macro calendar facts.

The completed 56-window history plan is never called here.  Each invocation
uses the one stable 90-day block containing the current New York date, so two
runs with unchanged provider facts have the same request scope and publish as
a semantic no-op.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import os
from typing import Final
from zoneinfo import ZoneInfo

from ..errors import ConflictError, ValidationError
from ..json_codec import dumps_strict
from .fmp_calendar_wholesale_history import (
    _stored_capture_payload,
    _wholesale_publication_result,
)
from .fmp_macro_calendar_history import (
    BACKFILL_END_DATE,
    MACRO_STORE,
    PROJECT_ROOT,
    FmpMacroCalendarHistoryRunner,
    FmpMacroCalendarWindow,
    _StdlibTransport,
    _domain_api,
    _utc_text,
)


REFRESH_ANCHOR_DATE: Final = BACKFILL_END_DATE + timedelta(days=1)
REFRESH_BLOCK_DAYS: Final = 90
_NEW_YORK: Final = ZoneInfo("America/New_York")


def build_fmp_macro_calendar_refresh_window(
    observed_at: datetime,
) -> FmpMacroCalendarWindow:
    """Return the stable, non-overlapping 90-day block for ``observed_at``."""

    if (
        not isinstance(observed_at, datetime)
        or observed_at.tzinfo is None
        or observed_at.utcoffset() is None
    ):
        raise ValidationError("FMP calendar refresh time must include an offset")
    local_date = observed_at.astimezone(_NEW_YORK).date()
    if local_date < REFRESH_ANCHOR_DATE:
        raise ValidationError("FMP calendar refresh precedes the completed history")
    block = (local_date - REFRESH_ANCHOR_DATE).days // REFRESH_BLOCK_DAYS
    start = REFRESH_ANCHOR_DATE + timedelta(days=block * REFRESH_BLOCK_DAYS)
    return FmpMacroCalendarWindow(
        start_date=start,
        end_date=start + timedelta(days=REFRESH_BLOCK_DAYS - 1),
    )


@dataclass(frozen=True, slots=True)
class FmpMacroCalendarRefreshReport:
    requested: int
    published: int
    unchanged: int
    written_versions: int
    start_date: date
    end_date: date
    employment_outcome: str = "unchanged"
    employment_written_versions: int = 0
    wholesale_outcome: str = "unchanged"
    wholesale_written_rows: int = 0

    def mapping(self) -> dict[str, object]:
        return {
            "requested": self.requested,
            "published": self.published,
            "unchanged": self.unchanged,
            "written_versions": self.written_versions,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "employment_outcome": self.employment_outcome,
            "employment_written_versions": self.employment_written_versions,
            "wholesale_outcome": self.wholesale_outcome,
            "wholesale_written_rows": self.wholesale_written_rows,
        }


class FmpMacroCalendarRefreshRunner(FmpMacroCalendarHistoryRunner):
    """Issue one current request after raw wholesale evidence is durable."""

    def __init__(
        self,
        *,
        wholesale_parser: Callable[..., object],
        wholesale_publisher_factory: Callable[[], object],
        employment_parser: Callable[..., object],
        employment_publisher_factory: Callable[[], object],
        attempt_observer: Callable[..., None] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._attempt_observer = attempt_observer
        self._fetch_completed_at: str | None = None
        self._raw_outcome = "failed"
        self._wholesale_parser = wholesale_parser
        self._wholesale_publisher_factory = wholesale_publisher_factory
        self._employment_parser = employment_parser
        self._employment_publisher_factory = employment_publisher_factory
        if (
            not callable(self._wholesale_parser)
            or not callable(self._wholesale_publisher_factory)
            or not callable(self._employment_parser)
            or not callable(self._employment_publisher_factory)
        ):
            raise ValidationError("FMP calendar refresh dependencies are invalid")

    @staticmethod
    def _require_wholesale_publisher(publisher: object, *, replay: bool) -> None:
        if (
            not callable(getattr(publisher, "publish", None))
            or (
                replay and not callable(getattr(publisher, "load_latest_window", None))
            )
        ):
            raise ConflictError("FMP wholesale calendar publisher is invalid")

    @staticmethod
    def _payload_for_window(
        capture: object,
        *,
        window: FmpMacroCalendarWindow,
    ) -> tuple[bytes, str, str, str]:
        body, captured_at, start_date, end_date = _stored_capture_payload(capture)
        if (start_date, end_date) != (
            window.start_date.isoformat(),
            window.end_date.isoformat(),
        ):
            raise ConflictError("FMP wholesale capture window is invalid")
        return body, captured_at, start_date, end_date

    def _publish_normalized(
        self,
        *,
        body: bytes,
        captured_at: str,
        window: FmpMacroCalendarWindow,
        wholesale_outcome: str,
        wholesale_written_rows: int,
        requested: int,
    ) -> FmpMacroCalendarRefreshReport:
        start_date = window.start_date.isoformat()
        end_date = window.end_date.isoformat()
        capture = self._parser(
            body,
            captured_at=captured_at,
            start_date=start_date,
            end_date=end_date,
        )
        employment_capture = self._employment_parser(
            body,
            captured_at=captured_at,
            start_date=start_date,
            end_date=end_date,
        )
        publisher = self._publisher_factory()
        employment_publisher = self._employment_publisher_factory()
        if (
            not callable(getattr(publisher, "publish", None))
            or not callable(getattr(employment_publisher, "publish", None))
        ):
            raise ConflictError("FMP calendar publisher is invalid")
        outcome, written_versions = self._publication_result(
            publisher.publish(capture)
        )
        employment_outcome, employment_written_versions = self._publication_result(
            employment_publisher.publish(employment_capture)
        )
        return FmpMacroCalendarRefreshReport(
            requested=requested,
            published=int(outcome == "published"),
            unchanged=int(outcome == "unchanged"),
            written_versions=written_versions,
            start_date=window.start_date,
            end_date=window.end_date,
            employment_outcome=employment_outcome,
            employment_written_versions=employment_written_versions,
            wholesale_outcome=wholesale_outcome,
            wholesale_written_rows=wholesale_written_rows,
        )

    def replay_latest_wholesale_window(self) -> FmpMacroCalendarRefreshReport | None:
        """Replay a retained current raw response without credential or network work."""

        observed_at = self._utcnow()
        window = build_fmp_macro_calendar_refresh_window(observed_at)
        wholesale_publisher = self._wholesale_publisher_factory()
        self._require_wholesale_publisher(wholesale_publisher, replay=True)
        capture = wholesale_publisher.load_latest_window(
            window.start_date.isoformat(),
            window.end_date.isoformat(),
        )
        if capture is None:
            return None
        body, captured_at, _, _ = self._payload_for_window(
            capture,
            window=window,
        )
        return self._publish_normalized(
            body=body,
            captured_at=captured_at,
            window=window,
            wholesale_outcome="replayed",
            wholesale_written_rows=0,
            requested=0,
        )

    def run(self) -> FmpMacroCalendarRefreshReport:
        """Run once; record polling outcomes separately from canonical evidence."""
        if self._attempt_observer is None:
            return self._run_once()
        started_at = _utc_text(self._utcnow())
        self._fetch_completed_at = None
        self._raw_outcome = "failed"
        try:
            report = self._run_once()
        except Exception:
            self._observe_attempt(started_at, "failed")
            raise
        outcome = "published" if report.published or report.employment_outcome == "published" else "unchanged"
        self._observe_attempt(started_at, outcome)
        return report

    def _observe_attempt(self, started_at: str, outcome: str) -> None:
        completed_at = _utc_text(self._utcnow())
        for source, source_outcome in (
            ("fmp_calendar_raw", self._raw_outcome),
            ("fmp_macro_calendar", outcome),
        ):
            self._attempt_observer(
                source=source, started_at=started_at, completed_at=completed_at,
                outcome=source_outcome, successful_fetch_at=self._fetch_completed_at,
                note="http_response_completed_refresh_outcome_includes_publication",
            )

    def _run_once(self) -> FmpMacroCalendarRefreshReport:
        """Run the approved one-request refresh in raw-first order."""

        wholesale_publisher = self._wholesale_publisher_factory()
        self._require_wholesale_publisher(wholesale_publisher, replay=False)
        observed_at = self._utcnow()
        captured_at = _utc_text(observed_at)
        window = build_fmp_macro_calendar_refresh_window(observed_at)

        # This remains the sole credential lookup and the sole transport call.
        # No normalized parser or publisher is reached until raw evidence has
        # been parsed and durably published.
        api_key = self._read_api_key_once()
        response = self._request(window, api_key)
        if self._attempt_observer is not None:
            self._fetch_completed_at = _utc_text(self._utcnow())
        wholesale_capture = self._wholesale_parser(
            response.body,
            captured_at=captured_at,
            start_date=window.start_date.isoformat(),
            end_date=window.end_date.isoformat(),
        )
        wholesale_outcome, wholesale_written_rows = _wholesale_publication_result(
            wholesale_publisher.publish(wholesale_capture)
        )
        self._raw_outcome = wholesale_outcome
        body, stored_captured_at, _, _ = self._payload_for_window(
            wholesale_capture,
            window=window,
        )
        return self._publish_normalized(
            body=body,
            captured_at=stored_captured_at,
            window=window,
            wholesale_outcome=wholesale_outcome,
            wholesale_written_rows=wholesale_written_rows,
            requested=1,
        )


def _employment_domain_api() -> tuple[Callable[..., object], Callable[[], object]]:
    """Lazily load the employment parser and canonical publisher factory."""

    from ..macro.fmp_release_surprises import (
        FmpEmploymentCalendarPublisher,
        parse_fmp_us_employment_calendar,
    )
    from ..registry import CANONICAL_REGISTRY_PATH, load_registry

    registry = load_registry(
        PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )

    def publisher_factory() -> object:
        return FmpEmploymentCalendarPublisher.for_canonical(registry=registry)

    return parse_fmp_us_employment_calendar, publisher_factory


def _wholesale_domain_api() -> tuple[Callable[..., object], Callable[[], object]]:
    """Lazily load the wholesale parser and canonical publisher factory."""

    from ..macro.fmp_calendar_wholesale import (
        FmpWholesaleCalendarPublisher,
        parse_fmp_us_calendar_wholesale,
    )
    from ..registry import CANONICAL_REGISTRY_PATH, load_registry

    registry = load_registry(
        PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )

    def publisher_factory() -> object:
        return FmpWholesaleCalendarPublisher.for_canonical(registry=registry)

    return parse_fmp_us_calendar_wholesale, publisher_factory


def refresh_fmp_macro_calendar_live() -> FmpMacroCalendarRefreshReport:
    """Run one approved canonical incremental refresh."""

    from .refresh_status import record_refresh_attempt

    parser, publisher_factory = _domain_api()
    employment_parser, employment_publisher_factory = _employment_domain_api()
    wholesale_parser, wholesale_publisher_factory = _wholesale_domain_api()
    return FmpMacroCalendarRefreshRunner(
        project_root=PROJECT_ROOT,
        macro_store=MACRO_STORE,
        parser=parser,
        publisher_factory=publisher_factory,
        wholesale_parser=wholesale_parser,
        wholesale_publisher_factory=wholesale_publisher_factory,
        employment_parser=employment_parser,
        employment_publisher_factory=employment_publisher_factory,
        transport=_StdlibTransport(),
        attempt_observer=lambda **attempt: record_refresh_attempt(
            PROJECT_ROOT / "data" / ".operations" / "refresh-status", **attempt
        ),
        credential_environment=os.environ,
        utcnow=lambda: datetime.now(timezone.utc),
        _canonical=True,
    ).run()


class _ArgumentFailure(Exception):
    pass


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _ArgumentFailure


def main(argv: list[str] | None = None) -> int:
    try:
        _SafeArgumentParser(add_help=False).parse_args(argv)
    except _ArgumentFailure:
        print(dumps_strict({"error": "invalid_arguments"}), file=os.sys.stderr)
        return 2
    report = refresh_fmp_macro_calendar_live()
    from .fetch_run_summary import record_report
    record_report('quant-data-fmp-macro-calendar.timer', report.mapping())
    print(dumps_strict(report.mapping()))
    return 0


if __name__ == "__main__":
    import sys
    from .fetch_run_history import run_recorded_cli
    raise SystemExit(run_recorded_cli(
        "quant-data-fmp-macro-calendar.timer", main, argv=sys.argv[1:]
    ))


__all__ = (
    "FmpMacroCalendarRefreshReport",
    "FmpMacroCalendarRefreshRunner",
    "REFRESH_ANCHOR_DATE",
    "REFRESH_BLOCK_DAYS",
    "build_fmp_macro_calendar_refresh_window",
    "refresh_fmp_macro_calendar_live",
)
