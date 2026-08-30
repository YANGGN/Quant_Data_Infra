"""Offline coverage for the paginated macro release-calendar v2 adapter."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from quant_data.errors import ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.macro.fmp_calendar_wholesale import (
    FmpWholesaleCalendarPublisher,
    parse_fmp_us_calendar_wholesale,
)
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry
from quant_data.stores import StoreMap
from quant_data.tool_platform.arguments import MacroReleaseCalendarArgumentsV1
from quant_data.tool_platform.context import (
    CapabilitySet,
    CancellationToken,
    Deadline,
    ExecutionBudget,
    FixedClock,
    ToolExecutionContext,
)
from quant_data.tool_platform.macro_access import (
    CALENDAR_OPERATION_VERSION,
    CALENDAR_TOOL_NAME,
    invoke_release_calendar,
    invoke_release_calendar_v2,
)
from tests.macro.test_fmp_calendar_wholesale import _body, _row


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
_CAPTURED_AT = "2026-08-21T00:00:00Z"


@dataclass(frozen=True, slots=True)
class _CalendarArgumentsV2:
    mode: str
    as_of: str | None
    date_only_policy: str
    limit: int
    start_date: str | None
    end_date: str | None
    event_name: str | None
    cursor: str | None = None


def _stores(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


def _fields(result: object) -> tuple[dict[str, object], ...]:
    return tuple(
        {field.name: field.value for field in record.fields}
        for record in getattr(result, "records")
    )


class MacroReleaseCalendarPaginationV2Tests(unittest.TestCase):
    """The successor pages the reconciled current selection without writes."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self._temporary.name)
        self.stores = _stores(self.root)
        self.registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        initialize_all(self.stores, self.registry)
        self.publisher = FmpWholesaleCalendarPublisher(
            macro_store=self.stores.macro,
            project_root=self.root,
            registry=self.registry,
        )
        self._publish(
            [
                _row("GDP Test", "2024-04-25 12:30:00", actual="1.5"),
                _row("CPI Test", "2024-05-01 12:30:00", actual="2.5"),
                _row("Jobs Test", "2024-05-03 12:30:00", actual="3.5"),
                _row("Retail Test", "2024-05-15 12:30:00", actual="4.5"),
            ],
            captured_at="2026-08-19T18:00:00Z",
        )
        self._publish(
            [_row("GDP Test", "2024-04-25 12:30:00", actual="2.0")],
            captured_at="2026-08-20T18:00:00Z",
        )
        self._baseline = mutation_fingerprint(self.stores)
        self._registry_sha256 = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()

    def tearDown(self) -> None:
        self.assertEqual(
            self._baseline["sha256"],
            mutation_fingerprint(self.stores)["sha256"],
        )
        self._temporary.cleanup()

    def _publish(self, rows: list[dict[str, object]], *, captured_at: str) -> None:
        result = self.publisher.publish(
            parse_fmp_us_calendar_wholesale(
                _body(rows),
                captured_at=captured_at,
                start_date="2024-04-01",
                end_date="2024-06-30",
            )
        )
        self.assertEqual(result.outcome, "published")

    def _context(self) -> ToolExecutionContext:
        return ToolExecutionContext(
            store_map=self.stores,
            registry_revision=self.registry.revision,
            registry_sha256=self._registry_sha256,
            request_id="macro-release-calendar-v2-test",
            api_version="1.0",
            tool_name=CALENDAR_TOOL_NAME,
            tool_version=CALENDAR_OPERATION_VERSION,
            operation_graph_id="tool_platform.macro.get_release_calendar.v2",
            operation_version=CALENDAR_OPERATION_VERSION,
            budget=ExecutionBudget(),
            capabilities=CapabilitySet(),
            deadline=Deadline(),
            cancellation=CancellationToken(),
            clock=FixedClock(Decimal("0"), _CAPTURED_AT),
        )

    @staticmethod
    def _arguments(
        *,
        mode: str = "latest",
        as_of: str | None = None,
        event_name: str | None = None,
        cursor: str | None = None,
        limit: int = 2,
    ) -> dict[str, object]:
        return {
            "mode": mode,
            "as_of": as_of,
            "date_only_policy": "completed_date",
            "limit": limit,
            "start_date": "2024-04-01",
            "end_date": "2024-06-30",
            "event_name": event_name,
            "cursor": cursor,
        }

    def _v2(self, arguments: object):
        return invoke_release_calendar_v2(
            CALENDAR_TOOL_NAME,
            arguments,
            self._context(),
            self.registry,
        )

    def test_keyset_pages_follow_reconciliation_and_are_terminal(self) -> None:
        first = self._v2(self._arguments(limit=2))
        self.assertTrue(first.truncation.applied)
        self.assertTrue(first.truncation.has_more)
        self.assertEqual(first.truncation.returned_count, 2)
        self.assertEqual(first.truncation.total_known_count, 4)
        self.assertIsInstance(first.truncation.next_cursor, str)

        second = self._v2(
            _CalendarArgumentsV2(
                **self._arguments(
                    cursor=first.truncation.next_cursor,
                    limit=1,
                )
            )
        )
        self.assertTrue(second.truncation.applied)
        self.assertTrue(second.truncation.has_more)
        self.assertEqual(second.truncation.returned_count, 1)
        self.assertIsInstance(second.truncation.next_cursor, str)

        terminal = self._v2(
            self._arguments(cursor=second.truncation.next_cursor, limit=10)
        )
        self.assertFalse(terminal.truncation.applied)
        self.assertFalse(terminal.truncation.has_more)
        self.assertIsNone(terminal.truncation.next_cursor)

        records = (*_fields(first), *_fields(second), *_fields(terminal))
        keys = [
            (
                str(record["event_at"]),
                str(record["event_name"]),
                str(record["event_id"]),
            )
            for record in records
        ]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(keys), 4)
        gdp = next(record for record in records if record["event_name"] == "GDP Test")
        self.assertEqual(gdp["source_model"], "incremental_event_version")
        self.assertEqual(json.loads(str(gdp["actual_json"])), "2.0")

    def test_cursor_is_canonical_and_bound_to_the_selection(self) -> None:
        first = self._v2(
            self._arguments(
                mode="as_of",
                as_of="2026-08-20T18:01:00Z",
                limit=1,
            )
        )
        cursor = first.truncation.next_cursor
        self.assertIsInstance(cursor, str)

        with self.assertRaises(ValidationError):
            self._v2(self._arguments(cursor="*"))
        with self.assertRaises(ValidationError):
            self._v2(self._arguments(cursor=f"{cursor}="))
        with self.assertRaises(ValidationError):
            self._v2(
                self._arguments(
                    mode="as_of",
                    as_of="2026-08-21T18:01:00Z",
                    cursor=cursor,
                )
            )
        with self.assertRaises(ValidationError):
            self._v2(
                self._arguments(
                    mode="as_of",
                    as_of="2026-08-20T18:01:00Z",
                    event_name="GDP",
                    cursor=cursor,
                )
            )

    def test_v1_selection_remains_nonpaginated_when_v2_has_no_cursor(self) -> None:
        v1_arguments = MacroReleaseCalendarArgumentsV1(
            mode="latest",
            as_of=None,
            date_only_policy="completed_date",
            limit=1,
            start_date="2024-04-01",
            end_date="2024-06-30",
            event_name=None,
        )
        v1 = invoke_release_calendar(
            CALENDAR_TOOL_NAME,
            v1_arguments,
            self._context(),
            self.registry,
        )
        v2 = self._v2(self._arguments(limit=1))

        self.assertEqual(v1.records, v2.records)
        self.assertEqual(v1.lineage, v2.lineage)
        self.assertTrue(v1.truncation.applied)
        self.assertTrue(v1.truncation.has_more)
        self.assertIsNone(v1.truncation.next_cursor)
        self.assertIsInstance(v2.truncation.next_cursor, str)


if __name__ == "__main__":
    unittest.main()
