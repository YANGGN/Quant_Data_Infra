"""Offline tests for the finite NY Fed primary-dealer operation."""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import date, datetime, timezone
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.macro.nyfed_primary_dealer_statistics import (
    parse_nyfed_primary_dealer_catalog,
    MAX_SERIES_KEYS,
    NyFedPrimaryDealerCatalogSeries,
    parse_nyfed_primary_dealer_statistics,
)
from quant_data.operations import nyfed_primary_dealer_statistics_history as op
from quant_data.operations.fmp_macro_calendar_history import (
    FmpMacroCalendarTransportResponse,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATALOG = (
    PROJECT_ROOT
    / "tests/fixtures/macro/nyfed_primary_dealer_catalog.json"
).read_bytes()
HISTORY = (
    PROJECT_ROOT
    / "tests/fixtures/macro/nyfed_primary_dealer_history.json"
).read_bytes()
FIXED_NOW = datetime(2026, 8, 20, 20, 15, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class _Publication:
    outcome: str
    written_series: int
    written_observation_versions: int


class _Publisher:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[object] = []

    def publish(self, capture: object) -> _Publication:
        self.events.append("publish")
        self.calls.append(capture)
        return _Publication("published", 4, 5)


class _Transport:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[dict[str, object]] = []

    def request(self, **kwargs: object) -> FmpMacroCalendarTransportResponse:
        self.calls.append(dict(kwargs))
        is_catalog = kwargs["url"] == op.NYFED_PRIMARY_DEALER_CATALOG_URL
        self.events.append("transport_catalog" if is_catalog else "transport_history")
        return FmpMacroCalendarTransportResponse(
            status=200,
            media_type="application/json; charset=utf-8",
            body=CATALOG if is_catalog else HISTORY,
        )


class NyFedPrimaryDealerHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.project = Path(self.temporary.name) / "project"
        self.store = self.project / "data" / "macro.sqlite"
        self.store.parent.mkdir(parents=True)
        self.store.write_bytes(b"fixture macro store")
        self.events: list[str] = []
        self.transport = _Transport(self.events)
        self.publisher = _Publisher(self.events)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _runner(
        self, times: list[float], *, canonical: bool = False
    ) -> op.NyFedPrimaryDealerHistoryRunner:
        def catalog_parser(body: bytes, *, series_break: str):
            self.events.append("catalog_parser")
            return parse_nyfed_primary_dealer_catalog(
                body, series_break=series_break
            )

        def parser(
            catalog_body: bytes,
            history_bodies: tuple[bytes, ...],
            **kwargs: object,
        ):
            self.events.append("parser")
            return parse_nyfed_primary_dealer_statistics(
                catalog_body, history_bodies, **kwargs
            )

        def publisher_factory() -> _Publisher:
            self.events.append("publisher_factory")
            return self.publisher

        clock = iter(times)
        return op.NyFedPrimaryDealerHistoryRunner(
            project_root=self.project,
            macro_store=self.store,
            catalog_parser=catalog_parser,
            parser=parser,
            publisher_factory=publisher_factory,
            transport=self.transport,
            utcnow=lambda: FIXED_NOW,
            monotonic=lambda: next(clock),
            _canonical=canonical,
        )

    def test_catalog_and_history_are_fetched_before_publication(self) -> None:
        report = self._runner([0.0, 0.5, 1.0]).run(
            series_break="SBN2024",
            start_date="2026-08-01",
            end_date="2026-08-31",
        )

        self.assertEqual(
            report.mapping(),
            {
                "requested": 2,
                "pages": 1,
                "selected_series": 4,
                "normalized_rows": 5,
                "published": 1,
                "unchanged": 0,
                "written_series": 4,
                "written_observation_versions": 5,
                "series_break": "SBN2024",
                "start_date": "2026-08-01",
                "end_date": "2026-08-31",
            },
        )
        self.assertEqual(
            self.events,
            [
                "transport_catalog",
                "catalog_parser",
                "transport_history",
                "parser",
                "publisher_factory",
                "publish",
            ],
        )
        self.assertEqual(len(self.transport.calls), 2)
        catalog_call, history_call = self.transport.calls
        self.assertEqual(catalog_call["parameters"], {})
        self.assertEqual(
            history_call["url"],
            (
                "https://markets.newyorkfed.org/api/pd/get/SBN2024/"
                "timeseries/PDPOSMBS-TOT_PDTRGS-EXTB_"
                "PDSORA-UTSETTOT_PDFTD-USTET.json"
            ),
        )
        self.assertEqual(history_call["parameters"], {})
        for call in self.transport.calls:
            self.assertEqual(
                call["headers"],
                {
                    "Accept": "application/json",
                    "User-Agent": "QuantDataInfra/1.0",
                },
            )
            self.assertEqual(call["timeout_seconds"], 60)
            self.assertEqual(call["max_bytes"], 16 * 1024 * 1024)
        self.assertEqual(len(self.publisher.calls), 1)

    def test_invalid_scope_stops_before_transport(self) -> None:
        runner = self._runner([0.0])
        with self.assertRaises(ValidationError):
            runner.run(
                series_break="latest",
                start_date="2026-08-01",
                end_date="2026-08-31",
            )
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.publisher.calls, [])

    def test_elapsed_bound_stops_after_single_attempts_before_publish(self) -> None:
        runner = self._runner(
            [0.0, 1.0, op.MAX_ELAPSED_SECONDS + 1]
        )
        with self.assertRaises(ResourceLimitError):
            runner.run(
                series_break="SBN2024",
                start_date="2026-08-01",
                end_date="2026-08-31",
            )
        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual(self.publisher.calls, [])
        self.assertNotIn("parser", self.events)


    def test_1539_selected_series_use_31_pages_and_32_requests(self) -> None:
        family_prefixes = (
            ("positions", "PDPOS"),
            ("transactions", "PDTR"),
            ("financing", "PDSORA"),
            ("settlement_fails", "PDFT"),
        )
        catalog = tuple(
            NyFedPrimaryDealerCatalogSeries(
                series_break="SBN2024",
                key_id=f"{family_prefixes[index % 4][1]}-{index:04d}",
                description=None,
                family=family_prefixes[index % 4][0],
                category=None,
                maturity=None,
            )
            for index in range(1_539)
        )
        batches = op._batches(catalog)
        self.assertEqual(MAX_SERIES_KEYS, 1_600)
        self.assertEqual(op.MAX_REQUESTS, 33)
        self.assertEqual(len(batches), 31)
        self.assertEqual([len(batch) for batch in batches], [50] * 30 + [39])

        parser_pages: list[tuple[bytes, ...]] = []

        def catalog_parser(
            body: bytes, *, series_break: str
        ) -> tuple[NyFedPrimaryDealerCatalogSeries, ...]:
            self.assertEqual((body, series_break), (CATALOG, "SBN2024"))
            return catalog

        def parser(
            catalog_body: bytes,
            history_bodies: tuple[bytes, ...],
            **kwargs: object,
        ) -> SimpleNamespace:
            self.assertEqual(catalog_body, CATALOG)
            self.assertEqual(kwargs["series_break"], "SBN2024")
            parser_pages.append(history_bodies)
            return SimpleNamespace(observations=())

        runner = op.NyFedPrimaryDealerHistoryRunner(
            project_root=self.project,
            macro_store=self.store,
            catalog_parser=catalog_parser,
            parser=parser,
            publisher_factory=lambda: self.publisher,
            transport=self.transport,
            utcnow=lambda: FIXED_NOW,
            monotonic=lambda: 0.0,
        )
        report = runner.run(
            series_break="SBN2024",
            start_date="2026-08-01",
            end_date="2026-08-31",
        )

        self.assertEqual(
            (report.requested, report.pages, report.selected_series),
            (32, 31, 1_539),
        )
        self.assertEqual(len(self.transport.calls), 32)
        self.assertEqual(len(parser_pages), 1)
        self.assertEqual(len(parser_pages[0]), 31)


    def test_canonical_runner_rejects_fixture_target(self) -> None:
        with self.assertRaisesRegex(ValidationError, "canonical target"):
            self._runner([0.0], canonical=True)

    def test_live_wrapper_and_cli_shape_are_network_free(self) -> None:
        report = op.NyFedPrimaryDealerHistoryReport(
            requested=2,
            pages=1,
            selected_series=4,
            normalized_rows=5,
            published=1,
            unchanged=0,
            written_series=4,
            written_observation_versions=5,
            series_break="SBN2024",
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31),
        )
        with (
            patch.object(op, "_require_canonical_target") as require_target,
            patch.object(
                op,
                "_domain_api",
                return_value=(object(), object(), object()),
            ) as domain_api,
            patch.object(op, "NyFedPrimaryDealerHistoryRunner") as runner_type,
        ):
            runner_type.return_value.run.return_value = report
            actual = op.populate_nyfed_primary_dealer_statistics_live(
                series_break="SBN2024",
                start_date="2026-08-01",
                end_date="2026-08-31",
            )

        self.assertIs(actual, report)
        require_target.assert_called_once_with()
        domain_api.assert_called_once_with()
        construction = runner_type.call_args.kwargs
        self.assertEqual(
            (construction["project_root"], construction["macro_store"]),
            (op.PROJECT_ROOT, op.MACRO_STORE),
        )
        self.assertIsInstance(construction["transport"], op._StdlibTransport)
        self.assertIs(construction["monotonic"], op.time.monotonic)
        self.assertTrue(construction["_canonical"])
        runner_type.return_value.run.assert_called_once_with(
            series_break="SBN2024",
            start_date="2026-08-01",
            end_date="2026-08-31",
        )

        with patch.object(
            op,
            "populate_nyfed_primary_dealer_statistics_live",
            return_value=report,
        ) as populate:
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    op.main(
                        [
                            "--series-break",
                            "SBN2024",
                            "--from",
                            "2026-08-01",
                            "--to",
                            "2026-08-31",
                        ]
                    ),
                    0,
                )
        self.assertEqual(json.loads(output.getvalue())["series_break"], "SBN2024")
        populate.assert_called_once_with(
            series_break="SBN2024",
            start_date="2026-08-01",
            end_date="2026-08-31",
        )

if __name__ == "__main__":
    unittest.main()
