"""Offline adversarial checks for Stage 10 history-extension evidence."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import stat
import tempfile
import unittest

from quant_data.errors import ConflictError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.market.fmp_bulk_daily_prices import (
    CapturedFmpStage10Response,
    FMP_STAGE10_DOWJONES_CONSTITUENT_PATH,
    FMP_STAGE10_NASDAQ_CONSTITUENT_PATH,
    FMP_STAGE10_SP500_CONSTITUENT_PATH,
    parse_fmp_stage10_price_response,
    parse_fmp_stage10_universe_response,
)
from quant_data.market.stage10_history_importer import (
    STAGE10_MIGRATION_ID,
    STAGE10_MIGRATION_SHA256,
    Stage10HistoryImporter,
)
from quant_data.market.stage10_history_windows import (
    STAGE10_HISTORY_WINDOWS,
    capture_fmp_stage10_window,
    prepare_fmp_stage10_window_capture,
)
from quant_data.market.stage10_scope import (
    REVIEWED_STAGE10_MANIFEST_SHA256,
    STAGE10_TARGET_PROFILE_ID,
    load_stage10_market_scope,
)
from quant_data.market.stage10_window_importer import Stage10HistoryWindowImporter
from quant_data.migrations import initialize_all
from quant_data.operations.backup import backup_all, restore_all
from quant_data.operations.stage10_history_extension_repopulation import (
    PrivateStage10HistoryExtensionCandidateState,
    Stage10HistoryExtensionCandidateReceipt,
    Stage10HistoryExtensionEvidence,
    Stage10HistoryExtensionReconciliation,
    _sidecar_bytes,
    build_stage10_history_extension_evidence,
    normalize_stage10_history_extension_ledger,
    reconcile_stage10_history_extension,
    validate_stage10_history_extension_prefix,
)
from quant_data.operations.stage10_history_extension_transition_402 import (
    CONTENT_TYPE as TRANSITION_402_CONTENT_TYPE,
    HTTP_STATUS as TRANSITION_402_HTTP_STATUS,
    RESPONSE_BYTE_COUNT as TRANSITION_402_RESPONSE_BYTE_COUNT,
    RESPONSE_SHA256 as TRANSITION_402_RESPONSE_SHA256,
    TRANSITION_TERMINAL_REASON as TRANSITION_402_TERMINAL_REASON,
)
from quant_data.operations.stage10_history_extension_transition_empty import (
    CONTENT_TYPE as TRANSITION_EMPTY_CONTENT_TYPE,
    HTTP_STATUS as TRANSITION_EMPTY_HTTP_STATUS,
    RESPONSE_BYTE_COUNT as TRANSITION_EMPTY_RESPONSE_BYTE_COUNT,
    RESPONSE_SHA256 as TRANSITION_EMPTY_RESPONSE_SHA256,
    TRANSITION_TERMINAL_REASON as TRANSITION_EMPTY_TERMINAL_REASON,
)
from quant_data.operations import stage10_history_extension_transition_empty_chain as transition_chain
from quant_data.operations.stage10_history_extension_transition_empty_chain import (
    KNOWN_LISTED_EMPTY_TRANSITION_SHA256,
    PRIOR_402_TRANSITION_SHA256,
    TRANSITION_CONTRACT,
    build_stage10_history_extension_authorization_chain,
    build_stage10_history_extension_authorization_proof,
)
from quant_data.operations.stage10_repopulation import (
    Stage10MarketReconciliation,
    stage10_failure_manifest_sha256,
)
from quant_data.registry import (
    CANONICAL_REGISTRY_PATH,
    load_registry,
    stage10_registry_profile,
    stage11_registry_profile,
)
from quant_data.stage1 import explicit_store_map
from quant_data.stores import StoreRole, read_connection


_BASE_COMPLETION_SHA256 = "c" * 64
_CODE_REVISION = transition_chain.stage10_history_extension_execution_revision()
_ATTEMPT_ID = "stage10-history-extension-focused-v1"
_AUTHORIZATION_CHAIN = build_stage10_history_extension_authorization_chain(
    {
        "contract": TRANSITION_CONTRACT,
        "transition_sha256": PRIOR_402_TRANSITION_SHA256,
    },
    {
        "contract": TRANSITION_CONTRACT,
        "transition_sha256": KNOWN_LISTED_EMPTY_TRANSITION_SHA256,
    },
)
_SUCCESSOR_TRANSITION_MATERIAL = {
    "authorization": transition_chain.TRANSITION_AUTHORIZATION,
    "authorization_chain": _AUTHORIZATION_CHAIN,
    "closed_request_count": transition_chain.CLOSED_REQUEST_COUNT,
    "contract": transition_chain.TRANSITION_CONTRACT,
    "live_manifest_sha256": transition_chain.LIVE_MANIFEST_SHA256,
    "new_execution_revision": _CODE_REVISION,
    "old_execution_revision": transition_chain.OLD_EXECUTION_REVISION,
    "pending": {
        "from": transition_chain.PENDING_FROM,
        "intent_id": transition_chain.PENDING_INTENT_ID,
        "intent_sha256": transition_chain.PENDING_INTENT_SHA256,
        "ordinal": transition_chain.PENDING_ORDINAL,
        "symbol": transition_chain.PENDING_SYMBOL,
        "to": transition_chain.PENDING_TO,
        "window_id": transition_chain.PENDING_WINDOW_ID,
    },
    "plan_sha256": transition_chain.PLAN_SHA256,
    "prefix_ledger_sha256": transition_chain.PREFIX_LEDGER_SHA256,
    "prefix_raw_sidecar_manifest_sha256": (
        transition_chain.PREFIX_RAW_SIDECAR_MANIFEST_SHA256
    ),
    "response": {
        "content_type": transition_chain.PENDING_CONTENT_TYPE,
        "http_status": transition_chain.PENDING_HTTP_STATUS,
        "response_byte_count": transition_chain.PENDING_RESPONSE_BYTE_COUNT,
        "response_sha256": transition_chain.PENDING_RESPONSE_SHA256,
    },
    "terminal_reason": TRANSITION_EMPTY_TERMINAL_REASON,
    "version": transition_chain.TRANSITION_VERSION,
}
_SUCCESSOR_TRANSITION = {
    **_SUCCESSOR_TRANSITION_MATERIAL,
    "transition_sha256": hashlib.sha256(
        dumps_strict(_SUCCESSOR_TRANSITION_MATERIAL).encode("utf-8")
    ).hexdigest(),
}
_AUTHORIZATION_PROOF = build_stage10_history_extension_authorization_proof(
    _SUCCESSOR_TRANSITION
)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SCOPE_PATH = _PROJECT_ROOT / "config" / "stage10_market_scope.json"
_MIGRATION_TIME = "2026-08-12T12:00:00Z"
_CAPTURE_TIME = datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc)
_UNIVERSE_IDS = (
    "sp500_current",
    "nasdaq100_current",
    "dow30_current",
    "curated_etfs",
    "major_indexes",
)


def _sha(value: object) -> str:
    return hashlib.sha256(dumps_strict(value).encode("utf-8")).hexdigest()


def _bytes_sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


_AAPL_WINDOW_RAW = dumps_strict(
    [
        {
            "symbol": "AAPL",
            "date": "1990-01-02",
            "open": 1,
            "high": 2,
            "low": 1,
            "close": 2,
            "volume": 1,
            "change": 1,
            "changePercent": 100,
            "vwap": 1,
        }
    ]
).encode("utf-8")
_EMPTY_WINDOW_RAW = b"[]"


class _StaticTransport:
    """Injected transport deliberately incapable of reaching the network."""

    def __init__(self, body: bytes) -> None:
        self._response = CapturedFmpStage10Response(200, "application/json", body)
        self.calls = 0

    def get(self, **_kwargs: object) -> CapturedFmpStage10Response:
        self.calls += 1
        return self._response


class Stage10HistoryExtensionRepopulationTests(unittest.TestCase):
    """Keep the extension ledger and candidate receipt deliberately closed."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.roster = ("AAPL",) + tuple(f"Z{number:03d}" for number in range(628))
        cls.failed_symbols = tuple(f"Z{number:03d}" for number in range(620, 628))
        cls.base = cls._build_base_reconciliation()
        cls.ledger, cls.sidecars = cls._build_ledger(cls.base)

    @classmethod
    def _build_base_reconciliation(
        cls,
        *,
        roster: tuple[str, ...] | None = None,
        failed_symbols: tuple[str, ...] | None = None,
        registry_source_sha256: str = "a" * 64,
    ) -> Stage10MarketReconciliation:
        resolved_roster = cls.roster if roster is None else roster
        resolved_failures = cls.failed_symbols if failed_symbols is None else failed_symbols
        failed_records = tuple(
            {
                "symbol": symbol,
                "reason": "operator_authorized_prior_failure",
                "provenance": "stage10_operator_authorized_seed",
            }
            for symbol in resolved_failures
        )
        price_coverage = tuple(
            {"provider_symbol": symbol}
            for symbol in resolved_roster
            if symbol not in set(resolved_failures)
        )
        values: dict[str, object] = {
            "scope_manifest_sha256": REVIEWED_STAGE10_MANIFEST_SHA256,
            "target_profile_id": STAGE10_TARGET_PROFILE_ID,
            "registry_schema_version": "1.6.0",
            "registry_version": "2.8.0",
            "registry_source_sha256": registry_source_sha256,
            "migration_id": STAGE10_MIGRATION_ID,
            "migration_sha256": STAGE10_MIGRATION_SHA256,
            "universe_summaries": tuple(
                {"universe_id": universe_id} for universe_id in _UNIVERSE_IDS
            ),
            "roster_count": 629,
            "roster_sha256": _sha({"roster": resolved_roster}),
            "failed_records": failed_records,
            "failed_tickers": resolved_failures,
            "failed_symbol_count": len(failed_records),
            "successful_symbol_count": len(price_coverage),
            "failure_manifest_sha256": stage10_failure_manifest_sha256(failed_records),
            "price_coverage": price_coverage,
            "universe_capture_count": 3,
            "price_capture_count": len(price_coverage),
            "current_price_count": len(price_coverage),
            "immutable_version_count": len(price_coverage),
            "historical_membership_inferred": False,
            "russell_excluded": True,
            "raw_captures_reparsed": True,
            "canonical_rows_match_raw": True,
            "correction_lineage_verified": True,
            "stores_unchanged": True,
        }
        material = {
            "contract": "quant_data.stage10_market_reconciliation",
            "contract_version": "1.1.0",
            "scope_manifest_sha256": values["scope_manifest_sha256"],
            "target_profile_id": values["target_profile_id"],
            "registry": {
                "schema_version": values["registry_schema_version"],
                "registry_version": values["registry_version"],
                "source_sha256": values["registry_source_sha256"],
            },
            "migration": {
                "id": values["migration_id"],
                "sha256": values["migration_sha256"],
            },
            "universe_summaries": list(values["universe_summaries"]),
            "roster_count": values["roster_count"],
            "roster_sha256": values["roster_sha256"],
            "failed_records": list(failed_records),
            "failed_tickers": list(values["failed_tickers"]),
            "failed_symbol_count": values["failed_symbol_count"],
            "successful_symbol_count": values["successful_symbol_count"],
            "failure_manifest_sha256": values["failure_manifest_sha256"],
            "price_coverage": list(price_coverage),
            "universe_capture_count": values["universe_capture_count"],
            "price_capture_count": values["price_capture_count"],
            "current_price_count": values["current_price_count"],
            "immutable_version_count": values["immutable_version_count"],
            "historical_membership_inferred": values["historical_membership_inferred"],
            "russell_excluded": values["russell_excluded"],
            "raw_captures_reparsed": values["raw_captures_reparsed"],
            "canonical_rows_match_raw": values["canonical_rows_match_raw"],
            "correction_lineage_verified": values["correction_lineage_verified"],
            "stores_unchanged": values["stores_unchanged"],
        }
        return Stage10MarketReconciliation(**values, sha256=_sha(material))

    @classmethod
    def _build_ledger(
        cls,
        base: Stage10MarketReconciliation,
        *,
        roster: tuple[str, ...] | None = None,
    ) -> tuple[list[dict[str, object]], dict[str, bytes]]:
        resolved_roster = cls.roster if roster is None else roster
        ordered = ("AAPL",) + tuple(
            symbol for symbol in resolved_roster if symbol != "AAPL"
        )
        records: list[dict[str, object]] = []
        sidecars: dict[str, bytes] = {}
        for ordinal, (window, symbol) in enumerate(
            (
                (window, symbol)
                for window in STAGE10_HISTORY_WINDOWS
                for symbol in ordered
            ),
            start=1,
        ):
            raw = _AAPL_WINDOW_RAW if ordinal == 1 else _EMPTY_WINDOW_RAW
            intent = _sha({"intent": ordinal})
            record: dict[str, object] = {
                "ordinal": ordinal,
                "symbol": symbol,
                "window_id": window.window_id,
                "from": window.start_date,
                "to": window.end_date,
                "query": {
                    "symbol": symbol,
                    "from": window.start_date,
                    "to": window.end_date,
                },
                "disposition": "complete" if ordinal == 1 else "complete_empty",
                "base_completion_sha256": _BASE_COMPLETION_SHA256,
                "base_reconciliation_sha256": base.sha256,
                "intent_sha256": intent,
                "result_sha256": _sha({"result": ordinal}),
                "response_sha256": _bytes_sha(raw),
                "response_byte_count": len(raw),
                "row_count": 1 if ordinal == 1 else 0,
            }
            if ordinal == 1:
                record.update(
                    {
                        "publication_receipt_sha256": _sha({"publication": ordinal}),
                        "semantic_identity": _sha({"semantic": ordinal}),
                    }
                )
            records.append(record)
            sidecars[intent] = raw
        return records, sidecars

    def _normalized(self, records: object | None = None):
        return normalize_stage10_history_extension_ledger(
            self.ledger if records is None else records,
            base_reconciliation=self.base,
            base_completion_sha256=_BASE_COMPLETION_SHA256,
            authorization_proof=_AUTHORIZATION_PROOF,
        )

    def _extension_reconciliation(
        self,
        *,
        operator_authorized: bool = False,
    ) -> Stage10HistoryExtensionReconciliation:
        failed_windows = (
            (
                {
                    "symbol": "EUV",
                    "window_id": "2025-2026",
                    "terminal_reason": TRANSITION_EMPTY_TERMINAL_REASON,
                },
            )
            if operator_authorized
            else ()
        )
        values: dict[str, object] = {
            "base_completion_sha256": _BASE_COMPLETION_SHA256,
            "base_reconciliation_sha256": self.base.sha256,
            "authorization_proof": _AUTHORIZATION_PROOF if operator_authorized else None,
            "scope_manifest_sha256": REVIEWED_STAGE10_MANIFEST_SHA256,
            "target_profile_id": STAGE10_TARGET_PROFILE_ID,
            "registry_source_sha256": self.base.registry_source_sha256,
            "roster_count": 629,
            "planned_window_count": 5032,
            "ledger_sha256": _sha(self.ledger),
            "raw_sidecar_manifest_sha256": _sha(sorted(self.sidecars)),
            "complete_window_count": 1,
            "empty_window_count": 5030 if operator_authorized else 5031,
            "terminal_failure_count": 1 if operator_authorized else 0,
            "failed_tickers": ("EUV",) if operator_authorized else (),
            "failed_windows": failed_windows,
            "extension_capture_count": 1,
            "current_price_count": 1,
            "immutable_version_count": 1,
            "price_coverage": tuple(
                {"provider_symbol": symbol, "row_count": 0}
                for symbol in self.roster
            ),
            "raw_sidecars_reparsed": True,
            "canonical_rows_match_raw": True,
            "correction_lineage_verified": True,
            "stores_unchanged": True,
        }
        material = {
            "contract": "quant_data.stage10_history_extension_reconciliation",
            "contract_version": "1.4.0",
            **{
                key: (list(value) if key == "failed_tickers" else list(value)
                if key in {"failed_windows", "price_coverage"} else value)
                for key, value in values.items()
            },
        }
        return Stage10HistoryExtensionReconciliation(**values, sha256=_sha(material))

    def _evidence(
        self, *, operator_authorized: bool = False
    ) -> Stage10HistoryExtensionEvidence:
        reconciliation = self._extension_reconciliation(
            operator_authorized=operator_authorized
        )
        values: dict[str, object] = {
            "reconciliation": reconciliation,
            "authorization_proof": reconciliation.to_primitive()[
                "authorization_proof"
            ],
            "cohort_registry_schema_version": "1.7.0",
            "cohort_registry_version": "2.9.0",
            "cohort_registry_source_sha256": "7" * 64,
            "source_health_sha256": "1" * 64,
            "restored_health_sha256": "2" * 64,
            "source_logical_manifest_sha256": "3" * 64,
            "restored_logical_manifest_sha256": "4" * 64,
            "source_mutation_before_sha256": "5" * 64,
            "source_mutation_after_sha256": "5" * 64,
            "restored_mutation_before_sha256": "6" * 64,
            "restored_mutation_after_sha256": "6" * 64,
            "source_reconciliation_sha256": reconciliation.sha256,
            "restored_reconciliation_sha256": reconciliation.sha256,
            "source_restored_equal": True,
            "source_reads_unchanged": True,
            "restored_reads_unchanged": True,
            "raw_reparsed_equal": True,
                "backup_restore_outcome": "validated_restored_cohort_equal",
        }
        material = {
            "contract": "quant_data.stage10_history_extension_evidence",
            "contract_version": "1.4.0",
            "reconciliation": reconciliation.to_primitive(),
            **{key: value for key, value in values.items() if key != "reconciliation"},
        }
        return Stage10HistoryExtensionEvidence(**values, sha256=_sha(material))

    @staticmethod
    def _universe_capture(path: str, symbols: tuple[str, ...]):
        return parse_fmp_stage10_universe_response(
            endpoint_path=path,
            body=dumps_strict(
                [
                    {"symbol": symbol, "name": f"Synthetic {symbol}"}
                    for symbol in symbols
                ]
            ).encode("utf-8"),
        )

    @classmethod
    def _exact_scope_captures(cls) -> dict[str, object]:
        """Build the required 519-equity union without a live universe call."""

        sp500 = (
            "AAPL",
            "MSFT",
            "NVDA",
            "JPM",
        ) + tuple(f"S{number:03d}" for number in range(1, 497))
        shared_nasdaq = ("AAPL",) + tuple(
            f"S{number:03d}" for number in range(1, 81)
        )
        nasdaq = (
            "ALAB",
            "CRWV",
            "LITE",
            "NBIS",
            "RKLB",
            "SNDK",
            "SPCX",
            "TER",
            "WMT",
        ) + tuple(f"N{number:03d}" for number in range(1, 11)) + shared_nasdaq
        dow = ("AAPL", "MSFT", "JPM") + tuple(
            f"S{number:03d}" for number in range(1, 28)
        )
        if (
            len(sp500) != 500
            or len(nasdaq) != 100
            or len(dow) != 30
            or len(set(sp500) | set(nasdaq) | set(dow)) != 519
        ):
            raise AssertionError("synthetic exact Stage 10 equity roster is invalid")
        return {
            "sp500_current": cls._universe_capture(
                FMP_STAGE10_SP500_CONSTITUENT_PATH, sp500
            ),
            "nasdaq100_current": cls._universe_capture(
                FMP_STAGE10_NASDAQ_CONSTITUENT_PATH, nasdaq
            ),
            "dow30_current": cls._universe_capture(
                FMP_STAGE10_DOWJONES_CONSTITUENT_PATH, dow
            ),
        }

    def test_normalizes_exact_5032_window_major_ledger_with_aapl_sentinel(self) -> None:
        normalized = self._normalized()

        self.assertEqual(len(normalized), 629 * 8)
        self.assertEqual(normalized[0]["ordinal"], 1)
        self.assertEqual(normalized[0]["symbol"], "AAPL")
        self.assertEqual(normalized[0]["window_id"], "1990-1994")
        self.assertEqual(normalized[0]["disposition"], "complete")
        self.assertEqual(normalized[628]["symbol"], "Z627")
        self.assertEqual(normalized[628]["window_id"], "1990-1994")
        self.assertEqual(normalized[629]["ordinal"], 630)
        self.assertEqual(normalized[629]["symbol"], "AAPL")
        self.assertEqual(normalized[629]["window_id"], "1995-1999")
        self.assertEqual(normalized[-1]["symbol"], "Z627")
        self.assertEqual(normalized[-1]["window_id"], "2025-2026")
        self.assertEqual(
            Counter(str(record["symbol"]) for record in normalized),
            Counter({symbol: 8 for symbol in self.roster}),
        )
        self.assertEqual(
            {str(record["symbol"]) for record in normalized}, set(self.roster)
        )
        with self.assertRaises(TypeError):
            normalized[0]["ordinal"] = 99  # type: ignore[index]

    def test_ledger_rejects_schedule_semantic_terminal_and_digest_tampering(self) -> None:
        cases: list[tuple[str, list[dict[str, object]]]] = []

        schedule = deepcopy(self.ledger)
        schedule[629]["from"] = "1995-01-02"
        cases.append(("schedule", schedule))

        semantic = deepcopy(self.ledger)
        semantic[0]["semantic_identity"] = "not-a-sha256"
        cases.append(("semantic identity", semantic))

        terminal = deepcopy(self.ledger)
        terminal_record = dict(terminal[1])
        terminal_record["disposition"] = "terminal_failure"
        terminal_record["terminal_reason"] = "provider_not_found"
        terminal_record["http_status"] = 410
        terminal_record["content_type"] = "application/json"
        terminal[1] = terminal_record
        cases.append(("terminal outcome", terminal))

        entitlement = deepcopy(self.ledger)
        entitlement_record = dict(entitlement[1])
        entitlement_record.update(
            {
                "disposition": "terminal_failure",
                "terminal_reason": TRANSITION_402_TERMINAL_REASON,
                "http_status": TRANSITION_402_HTTP_STATUS,
                "content_type": TRANSITION_402_CONTENT_TYPE,
                "response_sha256": TRANSITION_402_RESPONSE_SHA256,
                "response_byte_count": TRANSITION_402_RESPONSE_BYTE_COUNT,
                "row_count": 0,
            }
        )
        entitlement[1] = entitlement_record
        normalized_entitlement = self._normalized(entitlement)
        self.assertEqual(
            normalized_entitlement[1]["terminal_reason"],
            TRANSITION_402_TERMINAL_REASON,
        )
        with self.subTest("operator outcome requires receipt authorization proof"):
            with self.assertRaisesRegex(ValidationError, "authorization proof"):
                normalize_stage10_history_extension_ledger(
                    entitlement,
                    base_reconciliation=self.base,
                    base_completion_sha256=_BASE_COMPLETION_SHA256,
                )
        with self.subTest("operator outcome rejects tampered authorization proof"):
            tampered_chain = deepcopy(_AUTHORIZATION_PROOF)
            tampered_chain["sha256"] = "f" * 64
            with self.assertRaisesRegex(ValidationError, "authorization proof"):
                normalize_stage10_history_extension_ledger(
                    entitlement,
                    base_reconciliation=self.base,
                    base_completion_sha256=_BASE_COMPLETION_SHA256,
                    authorization_proof=tampered_chain,
                )
        with self.subTest("operator proof rejects recomputed foreign pending identity"):
            foreign = deepcopy(_AUTHORIZATION_PROOF)
            successor = foreign["successor_transition"]
            successor["pending"]["symbol"] = "AAPL"
            successor_material = {
                key: value
                for key, value in successor.items()
                if key != "transition_sha256"
            }
            successor["transition_sha256"] = _sha(successor_material)
            foreign["sha256"] = _sha(
                {
                    "contract": foreign["contract"],
                    "successor_transition": successor,
                    "version": foreign["version"],
                }
            )
            with self.assertRaisesRegex(ValidationError, "authorization proof"):
                normalize_stage10_history_extension_ledger(
                    entitlement,
                    base_reconciliation=self.base,
                    base_completion_sha256=_BASE_COMPLETION_SHA256,
                    authorization_proof=foreign,
                )

        with self.subTest("operator proof rejects recomputed foreign revision"):
            foreign = deepcopy(_AUTHORIZATION_PROOF)
            successor = foreign["successor_transition"]
            replacement = "0" * 64 if _CODE_REVISION != "0" * 64 else "1" * 64
            successor["new_execution_revision"] = replacement
            successor_material = {
                key: value
                for key, value in successor.items()
                if key != "transition_sha256"
            }
            successor["transition_sha256"] = _sha(successor_material)
            foreign["sha256"] = _sha(
                {
                    "contract": foreign["contract"],
                    "successor_transition": successor,
                    "version": foreign["version"],
                }
            )
            with self.assertRaisesRegex(ValidationError, "authorization proof"):
                normalize_stage10_history_extension_ledger(
                    entitlement,
                    base_reconciliation=self.base,
                    base_completion_sha256=_BASE_COMPLETION_SHA256,
                    authorization_proof=foreign,
                )

        entitlement_digest = deepcopy(entitlement)
        entitlement_digest[1]["response_sha256"] = "e" * 64
        cases.append(("entitlement response digest", entitlement_digest))

        entitlement_size = deepcopy(entitlement)
        entitlement_size[1]["response_byte_count"] = (
            TRANSITION_402_RESPONSE_BYTE_COUNT + 1
        )
        cases.append(("entitlement response size", entitlement_size))

        known_listed_empty = deepcopy(self.ledger)
        known_listed_empty_record = dict(known_listed_empty[1])
        known_listed_empty_record.update(
            {
                "disposition": "terminal_failure",
                "terminal_reason": TRANSITION_EMPTY_TERMINAL_REASON,
                "http_status": TRANSITION_EMPTY_HTTP_STATUS,
                "content_type": TRANSITION_EMPTY_CONTENT_TYPE,
                "response_sha256": TRANSITION_EMPTY_RESPONSE_SHA256,
                "response_byte_count": TRANSITION_EMPTY_RESPONSE_BYTE_COUNT,
                "row_count": 0,
            }
        )
        known_listed_empty[1] = known_listed_empty_record
        normalized_empty = self._normalized(known_listed_empty)
        self.assertEqual(
            normalized_empty[1]["terminal_reason"],
            TRANSITION_EMPTY_TERMINAL_REASON,
        )

        for label, field, value in (
            ("known-listed empty response digest", "response_sha256", "e" * 64),
            (
                "known-listed empty response size",
                "response_byte_count",
                TRANSITION_EMPTY_RESPONSE_BYTE_COUNT + 1,
            ),
            ("known-listed empty response status", "http_status", 204),
            ("known-listed empty response type", "content_type", "text/plain"),
        ):
            hostile = deepcopy(known_listed_empty)
            hostile[1][field] = value
            cases.append((label, hostile))

        digest = deepcopy(self.ledger)
        digest[1]["response_sha256"] = "x" * 64
        cases.append(("response digest", digest))

        duplicate_intent = deepcopy(self.ledger)
        duplicate_intent[1]["intent_sha256"] = duplicate_intent[0]["intent_sha256"]
        cases.append(("duplicate intent", duplicate_intent))

        for label, hostile in cases:
            with self.subTest(label=label):
                with self.assertRaises(ValidationError):
                    self._normalized(hostile)

    def test_sidecar_binding_rejects_missing_changed_and_non_bytes_content(self) -> None:
        record = self.ledger[0]
        intent = str(record["intent_sha256"])
        self.assertEqual(_sidecar_bytes(self.sidecars, intent, record), _AAPL_WINDOW_RAW)

        with self.subTest("missing sidecar"):
            with self.assertRaises(ConflictError):
                _sidecar_bytes({}, intent, record)
        with self.subTest("digest mismatch"):
            with self.assertRaises(ConflictError):
                _sidecar_bytes({intent: _EMPTY_WINDOW_RAW}, intent, record)
        with self.subTest("non-bytes sidecar"):
            with self.assertRaises(ValidationError):
                _sidecar_bytes({intent: "[]"}, intent, record)  # type: ignore[arg-type]

    def test_reconciles_exact_synthetic_cohort_with_window_lineage_and_no_read_mutation(self) -> None:
        """Exercise the public reconciliation against a real temporary SQLite cohort."""

        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            store_map = explicit_store_map(root / "stores")
            cohort_registry = stage11_registry_profile(
                load_registry(
                    _PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
                    project_root=_PROJECT_ROOT,
                    environment={},
                )
            )
            registry = stage10_registry_profile(cohort_registry)
            scope = load_stage10_market_scope(_SCOPE_PATH)
            initialize_all(store_map, cohort_registry, applied_at=_MIGRATION_TIME)
            history_importer = Stage10HistoryImporter(
                store_map,
                registry,
                scope,
                clock=lambda: _CAPTURE_TIME,
            )
            for prepared in history_importer.prepare_universe_publications(
                self._exact_scope_captures()  # type: ignore[arg-type]
            ):
                self.assertEqual(history_importer.publish_prepared(prepared).outcome, "succeeded")

            with read_connection(store_map, StoreRole.MARKET) as connection:
                roster = tuple(
                    str(row["provider_symbol"])
                    for row in connection.execute(
                        "SELECT provider_symbol FROM stage10_instruments "
                        "WHERE provider='fmp' ORDER BY provider_symbol"
                    )
                )
                asset_counts = tuple(
                    (str(row["asset_type"]), int(row["count"]))
                    for row in connection.execute(
                        "SELECT asset_type, count(*) AS count FROM stage10_instruments "
                        "GROUP BY asset_type ORDER BY asset_type"
                    )
                )
            self.assertEqual(len(roster), 629)
            self.assertEqual(asset_counts, (("equity", 519), ("etf", 95), ("index", 15)))
            failed_symbols = tuple(symbol for symbol in roster[-8:])
            base = self._build_base_reconciliation(
                roster=roster,
                failed_symbols=failed_symbols,
                registry_source_sha256=registry.source_sha256,
            )
            ledger, sidecars = self._build_ledger(base, roster=roster)
            # This synthetic roster intentionally has no reviewed listing-date
            # metadata. Model every non-sentinel provider response as an exact
            # terminal outcome instead of inventing a pre-listing boundary or
            # mutating the immutable instrument catalog.
            for record in ledger[1:]:
                record["disposition"] = "terminal_failure"
                record["terminal_reason"] = "provider_not_found"
                record["http_status"] = 404
                record["content_type"] = "application/json"
                raw = b'{"Error Message":"not found"}'
                record["response_sha256"] = _bytes_sha(raw)
                record["response_byte_count"] = len(raw)
                sidecars[str(record["intent_sha256"])] = raw

            with self.subTest("empty prefix before an extension publication"):
                before_empty_prefix = mutation_fingerprint(store_map)
                empty_prefix = validate_stage10_history_extension_prefix(
                    store_map,
                    registry,
                    scope,
                    base_reconciliation=base,
                    base_completion_sha256=_BASE_COMPLETION_SHA256,
                    ledger_records=(),
                    sidecar_raw_by_intent={},
                )
                after_empty_prefix = mutation_fingerprint(store_map)
                self.assertEqual(
                    before_empty_prefix["sha256"], after_empty_prefix["sha256"]
                )
                self.assertEqual(empty_prefix["closed_request_count"], 0)

            transport = _StaticTransport(_AAPL_WINDOW_RAW)
            capture = capture_fmp_stage10_window(
                prepare_fmp_stage10_window_capture("AAPL", STAGE10_HISTORY_WINDOWS[0]),
                "offline-window-key",
                transport,
            )
            self.assertEqual(transport.calls, 1)
            window_importer = Stage10HistoryWindowImporter(
                store_map,
                registry,
                scope,
                clock=lambda: _CAPTURE_TIME,
            )
            receipt = window_importer.publish_prepared(
                window_importer.prepare_window_capture(capture)
            )
            self.assertEqual(receipt.outcome, "succeeded")
            ledger[0]["semantic_identity"] = receipt.semantic_identity
            ledger[0]["publication_receipt_sha256"] = _sha(receipt.to_primitive())

            with self.subTest("one-record prefix after the AAPL sentinel"):
                prefix_sidecars = {
                    str(ledger[0]["intent_sha256"]): sidecars[
                        str(ledger[0]["intent_sha256"])
                    ]
                }
                before_prefix = mutation_fingerprint(store_map)
                prefix = validate_stage10_history_extension_prefix(
                    store_map,
                    registry,
                    scope,
                    base_reconciliation=base,
                    base_completion_sha256=_BASE_COMPLETION_SHA256,
                    ledger_records=ledger[:1],
                    sidecar_raw_by_intent=prefix_sidecars,
                )
                after_prefix = mutation_fingerprint(store_map)
                self.assertEqual(before_prefix["sha256"], after_prefix["sha256"])
                self.assertEqual(prefix["closed_request_count"], 1)

            before = mutation_fingerprint(store_map)
            reconciliation = reconcile_stage10_history_extension(
                store_map,
                registry,
                scope,
                base_reconciliation=base,
                base_completion_sha256=_BASE_COMPLETION_SHA256,
                ledger_records=ledger,
                sidecar_raw_by_intent=sidecars,
            )
            after = mutation_fingerprint(store_map)
            self.assertEqual(before["sha256"], after["sha256"])
            self.assertEqual(reconciliation.roster_count, 629)
            self.assertEqual(reconciliation.planned_window_count, 5032)
            self.assertEqual(
                (
                    reconciliation.complete_window_count,
                    reconciliation.empty_window_count,
                    reconciliation.terminal_failure_count,
                    reconciliation.extension_capture_count,
                    reconciliation.current_price_count,
                    reconciliation.immutable_version_count,
                ),
                (1, 0, 5031, 1, 1, 1),
            )
            self.assertTrue(reconciliation.raw_sidecars_reparsed)
            self.assertTrue(reconciliation.canonical_rows_match_raw)
            self.assertTrue(reconciliation.correction_lineage_verified)
            self.assertTrue(reconciliation.stores_unchanged)
            self.assertEqual(len(reconciliation.price_coverage), 629)

            with self.subTest("current cohort registry backup and evidence"):
                backup = backup_all(
                    store_map,
                    cohort_registry,
                    target_root=root / "backup",
                )
                restored = restore_all(
                    backup,
                    cohort_registry,
                    target_root=root / "restored",
                )
                evidence = build_stage10_history_extension_evidence(
                    store_map,
                    restored.store_map,
                    registry,
                    scope,
                    base_reconciliation=base,
                    base_completion_sha256=_BASE_COMPLETION_SHA256,
                    ledger_records=ledger,
                    sidecar_raw_by_intent=sidecars,
                    cohort_registry=cohort_registry,
                )
                self.assertEqual(
                    evidence.cohort_registry_source_sha256,
                    cohort_registry.source_sha256,
                )
                self.assertEqual(evidence.reconciliation, reconciliation)

            with self.subTest("publication receipt digest tamper"):
                hostile = deepcopy(ledger)
                hostile[0]["publication_receipt_sha256"] = "f" * 64
                with self.assertRaises(ValidationError):
                    reconcile_stage10_history_extension(
                        store_map,
                        registry,
                        scope,
                        base_reconciliation=base,
                        base_completion_sha256=_BASE_COMPLETION_SHA256,
                        ledger_records=hostile,
                        sidecar_raw_by_intent=sidecars,
                    )

            with self.subTest("empty window conflicts with legacy current fact"):
                legacy_body = dumps_strict(
                    [
                        {
                            "symbol": "AAPL",
                            "date": "1995-01-03",
                            "open": 2,
                            "high": 3,
                            "low": 2,
                            "close": 3,
                            "volume": 2,
                            "change": 1,
                            "changePercent": 50,
                            "vwap": 2,
                        }
                    ]
                ).encode("utf-8")
                legacy_capture = parse_fmp_stage10_price_response(
                    symbol="AAPL", body=legacy_body
                )
                self.assertEqual(
                    history_importer.publish_prepared(
                        history_importer.prepare_price_capture(legacy_capture)
                    ).outcome,
                    "succeeded",
                )
                hostile_empty = deepcopy(ledger)
                hostile_empty[629]["disposition"] = "complete_empty"
                hostile_empty[629].pop("terminal_reason")
                hostile_empty[629].pop("http_status")
                hostile_empty[629].pop("content_type")
                empty_raw = _EMPTY_WINDOW_RAW
                hostile_empty[629]["response_sha256"] = _bytes_sha(empty_raw)
                hostile_empty[629]["response_byte_count"] = len(empty_raw)
                hostile_sidecars = dict(sidecars)
                hostile_sidecars[
                    str(hostile_empty[629]["intent_sha256"])
                ] = empty_raw
                with self.assertRaisesRegex(
                    ValidationError, "empty window is not proven pre-listing"
                ):
                    reconcile_stage10_history_extension(
                        store_map,
                        registry,
                        scope,
                        base_reconciliation=base,
                        base_completion_sha256=_BASE_COMPLETION_SHA256,
                        ledger_records=hostile_empty,
                        sidecar_raw_by_intent=hostile_sidecars,
                    )

                authorized_empty = deepcopy(hostile_empty)
                authorized_empty[629]["disposition"] = "terminal_failure"
                authorized_empty[629]["terminal_reason"] = (
                    TRANSITION_EMPTY_TERMINAL_REASON
                )
                authorized_empty[629]["http_status"] = (
                    TRANSITION_EMPTY_HTTP_STATUS
                )
                authorized_empty[629]["content_type"] = (
                    TRANSITION_EMPTY_CONTENT_TYPE
                )
                before_authorized = mutation_fingerprint(store_map)
                authorized = reconcile_stage10_history_extension(
                    store_map,
                    registry,
                    scope,
                    base_reconciliation=base,
                    base_completion_sha256=_BASE_COMPLETION_SHA256,
                    authorization_proof=_AUTHORIZATION_PROOF,
                    ledger_records=authorized_empty,
                    sidecar_raw_by_intent=hostile_sidecars,
                )
                after_authorized = mutation_fingerprint(store_map)
                self.assertEqual(
                    before_authorized["sha256"],
                    after_authorized["sha256"],
                )
                self.assertIn("AAPL", authorized.failed_tickers)
                self.assertTrue(
                    any(
                        item["symbol"] == "AAPL"
                        and item["window_id"] == "1995-1999"
                        and item["terminal_reason"]
                        == TRANSITION_EMPTY_TERMINAL_REASON
                        for item in authorized.failed_windows
                    )
                )

    def test_private_candidate_receipt_is_closed_mode_restricted_and_no_replace(self) -> None:
        evidence = self._evidence()
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory) / "private-extension-candidate"
            state = PrivateStage10HistoryExtensionCandidateState(root)
            receipt = state.publish(
                evidence,
                code_revision=_CODE_REVISION,
                now=datetime(2026, 8, 12, 20, 0, tzinfo=timezone.utc),
                attempt_id=_ATTEMPT_ID,
            )
            receipt_path = root / "candidate-receipts" / f"{_ATTEMPT_ID}.json"
            self.assertTrue(receipt_path.is_file())
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(receipt_path.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(receipt_path.stat().st_mode), 0o600)
            payload = loads_strict(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(payload, receipt.to_primitive())
            self.assertEqual(
                set(payload),
                {
                    "contract",
                    "contract_version",
                    "attempt_id",
                    "generated_at",
                    "code_revision",
                    "candidate_state",
                    "evidence",
                    "evidence_sha256",
                    "receipt_sha256",
                },
            )
            self.assertEqual(payload["evidence"], evidence.to_primitive())
            self.assertNotIn(str(root), receipt_path.read_text(encoding="utf-8"))
            original = receipt_path.read_bytes()
            with self.assertRaises(ConflictError):
                state.publish(
                    evidence,
                    code_revision=_CODE_REVISION,
                    now=datetime(2026, 8, 12, 20, 1, tzinfo=timezone.utc),
                    attempt_id=_ATTEMPT_ID,
                )
            self.assertEqual(receipt_path.read_bytes(), original)
            self.assertFalse((root / "current").exists())

        operator_evidence = self._evidence(operator_authorized=True)
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            operator_root = Path(directory) / "operator-candidate"
            operator_receipt = PrivateStage10HistoryExtensionCandidateState(
                operator_root
            ).publish(
                operator_evidence,
                code_revision=_CODE_REVISION,
                now=datetime(2026, 8, 12, 20, 2, tzinfo=timezone.utc),
                attempt_id="stage10-history-extension-operator-v1",
            )
            self.assertEqual(
                operator_receipt.to_primitive()["evidence"][
                    "authorization_proof"
                ],
                _AUTHORIZATION_PROOF,
            )

        with self.subTest("candidate proof binds execution revision"):
            with self.assertRaisesRegex(ValidationError, "revision differs"):
                Stage10HistoryExtensionCandidateReceipt(
                    attempt_id="stage10-history-extension-proof-drift-v1",
                    generated_at="2026-08-12T20:03:00.000000Z",
                    code_revision="e" * 64,
                    candidate_state="private_candidate_only_no_operational_promotion",
                    evidence=operator_evidence.to_primitive(),
                    evidence_sha256=operator_evidence.sha256,
                    receipt_sha256="0" * 64,
                )

        with self.subTest("candidate rejects missing operator proof"):
            missing = deepcopy(operator_evidence.to_primitive())
            missing["authorization_proof"] = None
            missing["reconciliation"]["authorization_proof"] = None
            with self.assertRaisesRegex(ValidationError, "authorization proof"):
                Stage10HistoryExtensionCandidateReceipt(
                    attempt_id="stage10-history-extension-proof-missing-v1",
                    generated_at="2026-08-12T20:04:00.000000Z",
                    code_revision=_CODE_REVISION,
                    candidate_state="private_candidate_only_no_operational_promotion",
                    evidence=missing,
                    evidence_sha256=operator_evidence.sha256,
                    receipt_sha256="0" * 64,
                )


if __name__ == "__main__":  # pragma: no cover - direct focused test use
    unittest.main()
