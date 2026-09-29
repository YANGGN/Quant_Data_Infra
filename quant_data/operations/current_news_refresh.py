"""Fixed current-news refresh batch.

This module is separate from the frozen Stage 7 fixture job. It fixes the
source order and local stores, while the core importer owns parsing and
network-before-publication behavior. It accepts no provider, path, or schedule
arguments.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import time
from typing import Final

from ..credentials import read_project_credential
from ..errors import (
    ConflictError,
    MigrationError,
    RegistryError,
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from ..json_codec import dumps_strict
from ..migrations import migrate_and_register_store
from ..registry import CANONICAL_REGISTRY_PATH, Registry, load_registry
from ..stores import StoreMap, StoreRole


PROJECT_ROOT: Final = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
MARKET_STORE: Final = PROJECT_ROOT / "data" / "market.sqlite"
NEWS_STORE: Final = PROJECT_ROOT / "data" / "news.sqlite"
_VERSION: Final = "1.0.0"
_CONTRACT: Final = "quant_data.current_news_refresh"

_SOURCE_SPECS: Final = (
    ("fmp_stock_latest", ("FMP_API_KEY",), 1),
    ("fmp_press_releases", ("FMP_API_KEY",), 1),
    ("fmp_general", ("FMP_API_KEY",), 1),
    ("fed_press", (), 1),
    ("ecb_press", (), 1),
    ("bea_news", (), 1),
    ("eia_press", (), 1),
    ("alpaca_benzinga", ("ALPACA_API_KEY", "ALPACA_API_SECRET"), 1),
    ("finviz", (), 1),
    ("financialjuice", (), 1),
)
SOURCE_IDS: Final = tuple(item[0] for item in _SOURCE_SPECS)
_FAILURE_PRECEDENCE: Final = (64, 69, 74, 75, 70, 78)

Collector = Callable[[], object]
CredentialReader = Callable[..., str]


class _ArgumentFailure(Exception):
    pass


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _ArgumentFailure


@dataclass(frozen=True, slots=True)
class CurrentNewsRefreshStepReport:
    source: str
    request_cap: int
    attempted_requests: int
    successful_requests: int
    failed_requests: int
    outcome: str
    exit_code: int
    error: str | None = None
    coverage: dict[str, object] | None = None

    def mapping(self) -> dict[str, object]:
        result: dict[str, object] = {
            "source": self.source,
            "request_cap": self.request_cap,
            "attempted_requests": self.attempted_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "outcome": self.outcome,
            "exit_code": self.exit_code,
        }
        if self.error is not None:
            result["error"] = self.error
        if self.coverage is not None:
            result["coverage"] = self.coverage
        return result


@dataclass(frozen=True, slots=True)
class CurrentNewsRefreshReport:
    poll_slot: str
    steps: tuple[CurrentNewsRefreshStepReport, ...]
    exit_code: int

    def mapping(self) -> dict[str, object]:
        failed = sum(step.exit_code != 0 for step in self.steps)
        return {
            "contract": _CONTRACT,
            "version": _VERSION,
            "poll_slot": self.poll_slot,
            "request_cap": sum(step.request_cap for step in self.steps),
            "attempted_steps": len(self.steps),
            "failed_steps": failed,
            "unavailable_steps": sum(
                step.outcome == "unavailable" for step in self.steps
            ),
            "outcome": "succeeded" if failed == 0 else "partial",
            "exit_code": self.exit_code,
            "steps": [step.mapping() for step in self.steps],
        }


@dataclass(frozen=True, slots=True)
class _AlpacaBatchResult:
    request_cap: int
    attempted_requests: int
    successful_requests: int
    failed_requests: int
    exit_code: int
    error: str | None = None
    coverage: dict[str, object] | None = None


def _utc_text(value: datetime, *, hour: bool = False) -> str:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValidationError("Current news refresh time must include an offset")
    value = value.astimezone(timezone.utc)
    if hour:
        value = value.replace(minute=0, second=0, microsecond=0)
        return value.isoformat(timespec="seconds").replace("+00:00", "Z")
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _failure_details(error: Exception) -> tuple[int, str]:
    if isinstance(error, ValidationError):
        return 64, "invalid_request"
    if isinstance(error, StoreUnavailableError):
        return 69, "store_unavailable"
    if isinstance(error, ResourceLimitError):
        return 74, "local_io"
    if isinstance(error, (RegistryError, MigrationError, ConflictError)):
        return 75, "temporary_conflict"
    return 70, "internal_failure"


def _aggregate_exit_code(steps: Sequence[CurrentNewsRefreshStepReport]) -> int:
    failed = {step.exit_code for step in steps if step.exit_code}
    return next((code for code in _FAILURE_PRECEDENCE if code in failed), 0)


def _validate_collectors(collectors: Mapping[str, Collector]) -> dict[str, Collector]:
    if not isinstance(collectors, Mapping) or set(collectors) != set(SOURCE_IDS):
        raise ValidationError("Current news refresh collectors are invalid")
    result: dict[str, Collector] = {}
    for source in SOURCE_IDS:
        collector = collectors[source]
        if not callable(collector):
            raise ValidationError("Current news refresh collectors are invalid")
        result[source] = collector
    return result


def _credentials_available(
    names: tuple[str, ...],
    *,
    environment: Mapping[str, str],
    credential_reader: CredentialReader,
    known: dict[str, bool],
) -> bool:
    for name in names:
        if name not in known:
            try:
                credential_reader(
                    project_root=PROJECT_ROOT,
                    name=name,
                    environment=environment,
                )
            except Exception:
                known[name] = False
            else:
                known[name] = True
        if not known[name]:
            return False
    return True


def _successful_step(
    source: str,
    request_cap: int,
    result: object,
) -> CurrentNewsRefreshStepReport:
    if isinstance(result, _AlpacaBatchResult):
        if (
            result.request_cap < 0
            or (result.request_cap == 0 and not (
                source == "alpaca_benzinga" and result.coverage is not None
                and result.exit_code == 69 and result.error == "selected_symbols_unavailable"
                and result.attempted_requests == 0
            ))
            or result.attempted_requests < 0
            or result.successful_requests < 0
            or result.failed_requests < 0
            or result.attempted_requests
            != result.successful_requests + result.failed_requests
            or result.attempted_requests > result.request_cap
            or result.exit_code not in (*_FAILURE_PRECEDENCE, 0)
        ):
            raise ValidationError("Current news Alpaca result is invalid")
        return CurrentNewsRefreshStepReport(
            source=source,
            request_cap=result.request_cap,
            attempted_requests=result.attempted_requests,
            successful_requests=result.successful_requests,
            failed_requests=result.failed_requests,
            outcome=("unavailable" if result.request_cap == 0 else
                     "succeeded" if result.exit_code == 0 else "partial"),
            exit_code=result.exit_code,
            error=result.error,
            coverage=result.coverage,
        )
    outcome = getattr(result, "outcome", None)
    if outcome not in {"succeeded", "unchanged", "published"}:
        raise ValidationError("Current news source result is invalid")
    return CurrentNewsRefreshStepReport(
        source=source,
        request_cap=request_cap,
        attempted_requests=1,
        successful_requests=1,
        failed_requests=0,
        outcome=outcome,
        exit_code=0,
    )


def run_current_news_refresh(
    *,
    collectors: Mapping[str, Collector],
    environment: Mapping[str, str],
    utcnow: Callable[[], datetime],
    credential_reader: CredentialReader = read_project_credential,
) -> CurrentNewsRefreshReport:
    """Run every fixed profile once and continue after independent failures."""

    if not callable(utcnow) or not callable(credential_reader):
        raise ValidationError("Current news refresh dependencies are invalid")
    bound = _validate_collectors(collectors)
    poll_slot = _utc_text(utcnow(), hour=True)
    known: dict[str, bool] = {}
    steps: list[CurrentNewsRefreshStepReport] = []
    for source, credential_names, request_cap in _SOURCE_SPECS:
        if not _credentials_available(
            credential_names,
            environment=environment,
            credential_reader=credential_reader,
            known=known,
        ):
            steps.append(
                CurrentNewsRefreshStepReport(
                    source=source,
                    request_cap=request_cap,
                    attempted_requests=0,
                    successful_requests=0,
                    failed_requests=0,
                    outcome="unavailable",
                    exit_code=78,
                    error="credential_unavailable",
                )
            )
            continue
        try:
            step = _successful_step(
                source,
                request_cap,
                bound[source](),
            )
        except Exception as error:
            exit_code, error_name = _failure_details(error)
            step = CurrentNewsRefreshStepReport(
                source=source,
                request_cap=request_cap,
                attempted_requests=1,
                successful_requests=0,
                failed_requests=1,
                outcome="failed",
                exit_code=exit_code,
                error=error_name,
            )
        steps.append(step)
    result = tuple(steps)
    return CurrentNewsRefreshReport(
        poll_slot=poll_slot,
        steps=result,
        exit_code=_aggregate_exit_code(result),
    )


def _fixed_store_map() -> StoreMap:
    if (
        not PROJECT_ROOT.is_absolute()
        or MARKET_STORE != PROJECT_ROOT / "data" / "market.sqlite"
        or NEWS_STORE != PROJECT_ROOT / "data" / "news.sqlite"
        or not PROJECT_ROOT.is_dir()
        or not MARKET_STORE.is_file()
        or not NEWS_STORE.is_file()
    ):
        raise StoreUnavailableError("Current news refresh target is unavailable")
    return StoreMap.four_explicit(
        market=MARKET_STORE,
        macro=PROJECT_ROOT / "data" / "macro.sqlite",
        company=PROJECT_ROOT / "data" / "company.sqlite",
        news=NEWS_STORE,
    )


def _prepare_live_dependencies(
    observed_at: datetime,
) -> tuple[StoreMap, Registry]:
    stores = _fixed_store_map()
    registry = load_registry(
        PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )
    migrate_and_register_store(
        stores,
        registry,
        StoreRole.NEWS,
        applied_at=_utc_text(observed_at),
    )
    return stores, registry


def _credential(name: str, environment: Mapping[str, str]) -> str:
    return read_project_credential(
        project_root=PROJECT_ROOT,
        name=name,
        environment=environment,
    )


def _run_alpaca_batches(
    batches: Sequence[tuple[str, ...]],
    invoke: Callable[[tuple[str, ...]], object],
    *, selected_coverage=None, max_run_seconds=1800, monotonic=time.monotonic,
    sleep=time.sleep,
) -> _AlpacaBatchResult:
    if not batches and selected_coverage is None:
        raise StoreUnavailableError("Current market coverage is unavailable")
    from ..news.selected_market_coverage import (
        SELECTED_ALPACA_NEWS_BATCH_SIZE, SELECTED_ALPACA_NEWS_MAX_BATCHES)
    if selected_coverage is not None and (
        len(batches) > SELECTED_ALPACA_NEWS_MAX_BATCHES
        or any(not 1 <= len(b) <= SELECTED_ALPACA_NEWS_BATCH_SIZE for b in batches)
    ):
        raise ResourceLimitError("Selected news batches exceed their 10-symbol or 6600-symbol total bound")
    started=monotonic();attempted=0;full_pages=0
    next_request_at = started
    successes = 0
    failures: list[tuple[int, str]] = []
    for batch in batches:
        if selected_coverage is not None:
            # Smaller groups need more requests. Pace starts without retries or
            # extending the invocation budget, including after failed requests.
            earliest_start = max(monotonic(), next_request_at)
            if earliest_start - started + 60 > max_run_seconds:
                break
            while True:
                remaining_wait = earliest_start - monotonic()
                if remaining_wait <= 0:
                    break
                sleep(remaining_wait)
            if monotonic() - started + 60 > max_run_seconds:
                break
            next_request_at = monotonic() + 0.5
        attempted+=1
        try:
            receipt=invoke(batch)
            outcome = getattr(receipt, "outcome", None)
            if getattr(receipt,"provider_row_count",0)>=50:full_pages+=1
            if outcome not in {"succeeded", "unchanged", "published"}:
                raise ValidationError("Current news Alpaca result is invalid")
        except Exception as error:
            failures.append(_failure_details(error))
        else:
            successes += 1
    if selected_coverage is not None:
        remaining=len(batches)-attempted
        coverage={**selected_coverage.report(),"requested_batches":attempted,"deferred_batches":remaining,
            "response_limit":50,"pages_per_batch":1,"full_response_pages":full_pages,
            "minimum_request_interval_seconds":0.5,
            "pagination_status":"first_page_only","time_window_completeness":"unproven",
            "request_start_deadline_seconds":max_run_seconds,"per_response_byte_limit":64*1024*1024,
            "global_feed_requests":9,"maximum_total_requests":len(batches)+9,
            "maximum_total_response_bytes":(len(batches)+9)*64*1024*1024}
        if not batches:
            return _AlpacaBatchResult(0,0,0,0,69,"selected_symbols_unavailable",coverage)
        codes={code for code,_ in failures}
        if remaining:codes.add(74)
        exit_code=next((code for code in _FAILURE_PRECEDENCE if code in codes),0)
        return _AlpacaBatchResult(len(batches),attempted,successes,len(failures),exit_code,
            "invocation_budget" if remaining else next((name for code,name in failures if code==exit_code),None),coverage)
    if not failures:
        return _AlpacaBatchResult(
            request_cap=len(batches),
            attempted_requests=len(batches),
            successful_requests=successes,
            failed_requests=0,
            exit_code=0,
        )
    codes = {code for code, _ in failures}
    exit_code = next(code for code in _FAILURE_PRECEDENCE if code in codes)
    return _AlpacaBatchResult(
        request_cap=len(batches),
        attempted_requests=len(batches),
        successful_requests=successes,
        failed_requests=len(failures),
        exit_code=exit_code,
        error=next(name for code, name in failures if code == exit_code),
    )


def _live_collectors(
    *,
    stores: StoreMap,
    registry: Registry,
    environment: Mapping[str, str],
    observed_at: datetime,
    website_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Collector]:
    """Import live bindings lazily so unit tests remain offline."""

    from ..news.selected_market_coverage import read_host_news_coverage
    from ..news.current_market_coverage import read_current_market_news_coverage
    from ..news.current_multi_source import (
        CurrentMultiSourceCredentials,
        CurrentMultiSourceImporter,
        CurrentMultiSourceRequest,
        StdlibCurrentMultiSourceTransport,
    )
    from .fmp_stock_latest_news_refresh import refresh_fmp_stock_latest_news

    invocation_started = monotonic()
    coverage_error = None
    try:
        coverage = read_host_news_coverage(stores,cutoff=_utc_text(observed_at))
    except (ConflictError,ResourceLimitError,StoreUnavailableError,ValidationError) as error:
        coverage = None
        coverage_error = error
    frozen_clock = lambda: observed_at
    importer = CurrentMultiSourceImporter(stores, registry, clock=frozen_clock)
    website_importer = CurrentMultiSourceImporter(stores, registry, clock=website_clock)
    transport = StdlibCurrentMultiSourceTransport()

    def run_feed(feed_id: str, symbols: tuple[str, ...] | None = None) -> object:
        credentials = CurrentMultiSourceCredentials(
            fmp_api_key=(
                _credential("FMP_API_KEY", environment)
                if feed_id in {"fmp_press_releases", "fmp_general"}
                else None
            ),
            alpaca_api_key=(
                _credential("ALPACA_API_KEY", environment)
                if feed_id == "alpaca_benzinga"
                else None
            ),
            alpaca_api_secret=(
                _credential("ALPACA_API_SECRET", environment)
                if feed_id == "alpaca_benzinga"
                else None
            ),
        )
        selected_importer = website_importer if feed_id in {"finviz", "financialjuice"} else importer
        return selected_importer.run_once(
            request=CurrentMultiSourceRequest(
                feed_id=feed_id,
                poll_slot=observed_at,
                symbols=symbols,
            ),
            credentials=credentials,
            transport=transport,
        )

    def run_alpaca() -> _AlpacaBatchResult:
        if coverage_error is not None:
            raise coverage_error
        selected_coverage = coverage if coverage is not None else read_current_market_news_coverage(stores)
        return _run_alpaca_batches(
            tuple(tuple(batch) for batch in selected_coverage.alpaca_symbol_batches),
            lambda batch: run_feed("alpaca_benzinga", batch),
            selected_coverage=coverage,
            # Reserve the final two website deadlines and one minute of local
            # work inside the existing 30-minute service timeout.
            max_run_seconds=max(0, 1800 - (monotonic() - invocation_started) - 180),
            monotonic=monotonic,
            sleep=sleep,
        )

    return {
        "fmp_stock_latest": lambda: refresh_fmp_stock_latest_news(
            environment=environment,
            clock=frozen_clock,
        ),
        "fmp_press_releases": lambda: run_feed("fmp_press_releases"),
        "fmp_general": lambda: run_feed("fmp_general"),
        "fed_press": lambda: run_feed("fed_press"),
        "ecb_press": lambda: run_feed("ecb_press"),
        "bea_news": lambda: run_feed("bea_news"),
        "eia_press": lambda: run_feed("eia_press"),
        "alpaca_benzinga": run_alpaca,
        "finviz": lambda: run_feed("finviz"),
        "financialjuice": lambda: run_feed("financialjuice"),
    }


def refresh_current_news_live() -> CurrentNewsRefreshReport:
    """Run one fixed current-news batch against the canonical local stores."""

    observed_at = datetime.now(timezone.utc)
    stores, registry = _prepare_live_dependencies(observed_at)
    return run_current_news_refresh(
        collectors=_live_collectors(
            stores=stores,
            registry=registry,
            environment=os.environ,
            observed_at=observed_at,
        ),
        environment=os.environ,
        utcnow=lambda: observed_at,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        _SafeArgumentParser(add_help=False).parse_args(argv)
    except _ArgumentFailure:
        sys.stderr.write(dumps_strict({"error": "invalid_arguments"}) + "\n")
        return 2
    try:
        report = refresh_current_news_live()
    except Exception as error:
        exit_code, error_name = _failure_details(error)
        sys.stderr.write(
            dumps_strict(
                {
                    "contract": f"{_CONTRACT}_error",
                    "version": _VERSION,
                    "error": error_name,
                    "exit_code": exit_code,
                }
            )
            + "\n"
        )
        return exit_code
    from .fetch_run_summary import record_report
    record_report('quant-data-current-news-refresh.timer', report.mapping())
    sys.stdout.write(dumps_strict(report.mapping()) + "\n")
    return report.exit_code


if __name__ == "__main__":  # pragma: no cover
    from .fetch_run_history import run_recorded_cli
    raise SystemExit(run_recorded_cli(
        "quant-data-current-news-refresh.timer", main, argv=sys.argv[1:]
    ))


__all__ = (
    "CurrentNewsRefreshReport",
    "CurrentNewsRefreshStepReport",
    "SOURCE_IDS",
    "refresh_current_news_live",
    "run_current_news_refresh",
)
