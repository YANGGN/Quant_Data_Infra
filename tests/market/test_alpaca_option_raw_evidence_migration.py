from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from quant_data.migrations import initialize_all, migrate_and_register_store
from quant_data.registry import (
    load_registry,
    option_raw_evidence_registry_profile,
)
from quant_data.stores import StoreMap, StoreRole, read_connection, writer_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
AT = "2026-08-28T20:05:00.000000Z"


def _stores(root: Path) -> StoreMap:
    data = root / "data"
    data.mkdir()
    return StoreMap.four_explicit(
        market=data / "market.sqlite",
        macro=data / "macro.sqlite",
        company=data / "company.sqlite",
        news=data / "news.sqlite",
    )


def _deliverable(root_symbol: str, amount: str = "100") -> str:
    return json.dumps(
        {
            "deliverables": [
                {
                    "allocation_percentage": "100",
                    "amount": amount,
                    "delayed_settlement": False,
                    "symbol": root_symbol,
                    "type": "equity",
                }
            ],
            "root_symbol": root_symbol,
            "size": 100,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


class AlpacaOptionRawEvidenceMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        self.stores = _stores(self.root)
        self.current = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        self.predecessor = option_raw_evidence_registry_profile(self.current)
        initialize_all(self.stores, self.predecessor)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _seed_pre_migration_rows(self) -> None:
        with writer_connection(self.stores, StoreRole.MARKET) as connection:
            connection.execute(
                """
                INSERT INTO ingestion_runs(
                    run_id, dataset_id, semantic_identity, command, scope_json,
                    status, started_at, code_version
                ) VALUES (
                    'legacy-option-run', 'fixture.market.options', ?,
                    'fixture.seed', '{}', 'running', ?, 'fixture'
                )
                """,
                ("1" * 64, AT),
            )
            connection.execute(
                """
                INSERT INTO instruments(
                    instrument_id, asset_type, created_run_id, canonical_symbol,
                    display_name, exchange, currency, country, first_seen_at,
                    last_seen_at, active
                ) VALUES (
                    'spy', 'etf', 'legacy-option-run', 'SPY', 'SPY ETF',
                    'ARCX', 'USD', 'US', ?, ?, 1
                )
                """,
                (AT, AT),
            )
            connection.executemany(
                """
                INSERT INTO ingestion_run_outputs(
                    run_id, dataset_id, semantic_identity
                ) VALUES ('legacy-option-run', ?, ?)
                """,
                (
                    ("fixture.market.option_capture_evidence", "1" * 64),
                    ("fixture.market.options", "1" * 64),
                ),
            )
            connection.execute(
                """
                INSERT INTO ingestion_artifacts(
                    artifact_id, run_id, dataset_id, content_sha256, media_type,
                    byte_count, source_reference, request_scope_json, captured_at,
                    captured_precision, normalization_version
                ) VALUES (
                    'legacy-option-artifact', 'legacy-option-run',
                    'fixture.market.option_capture_evidence', ?,
                    'application/json', 1, 'fixture', '{}', ?, 'datetime', 'fixture'
                )
                """,
                ("2" * 64, AT),
            )
            connection.execute(
                """
                INSERT INTO ingestion_snapshots(
                    snapshot_id, run_id, dataset_id, semantic_identity, scope_json,
                    completeness, row_count, captured_at, captured_precision,
                    validation_state, warnings_json
                ) VALUES (
                    'legacy-option-snapshot', 'legacy-option-run',
                    'fixture.market.options', ?, '{}', 'complete', 3, ?,
                    'datetime', 'validated', '[]'
                )
                """,
                ("1" * 64, AT),
            )
            connection.execute(
                """
                INSERT INTO option_surface_captures(
                    capture_id, semantic_identity, underlying_instrument_id,
                    requested_feed, resolved_feed, environment, request_scope_json,
                    requested_at, requested_precision, completed_at,
                    completed_precision, available_at, available_precision,
                    completeness, deliverable_policy, artifact_id,
                    source_snapshot_id, run_id
                ) VALUES (
                    'legacy-option-capture', ?, 'spy', 'indicative', 'indicative',
                    'paper', '{}', ?, 'datetime', ?, 'datetime', ?, 'datetime',
                    'complete', 'standard_only', 'legacy-option-artifact',
                    'legacy-option-snapshot', 'legacy-option-run'
                )
                """,
                ("4" * 64, AT, AT, AT),
            )
            for index, (root_symbol, amount) in enumerate(
                (("SPY", "100"), ("QQQ", "100"), ("SPY", "100junk")),
                start=1,
            ):
                contract_id = f"contract-{index}"
                connection.execute(
                    """
                    INSERT INTO option_contracts(
                        contract_id, underlying_instrument_id, provider,
                        provider_contract_id, contract_symbol, expiration_date,
                        strike_price, option_type, deliverable_kind,
                        contract_multiplier, nonstandard_deliverable_json,
                        contract_status, available_at, available_precision,
                        created_run_id
                    ) VALUES (?, 'spy', 'alpaca', ?, ?, '2026-09-18', '650',
                              'call', 'nonstandard', 100, ?, 'active', ?,
                              'datetime', 'legacy-option-run')
                    """,
                    (
                        contract_id,
                        f"provider-{index}",
                        f"{root_symbol}260918C00650000",
                        _deliverable(root_symbol, amount),
                        AT,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO option_surface_snapshots(
                        surface_snapshot_id, capture_id, contract_id, surface_state,
                        missing_reason, exclusion_reason, available_at,
                        available_precision, source_row
                    ) VALUES (?, 'legacy-option-capture', ?, 'excluded', NULL,
                              'nonstandard_deliverable', ?, 'datetime', ?)
                    """,
                    (f"surface-{index}", contract_id, AT, index),
                )
                connection.execute(
                    """
                    INSERT INTO option_open_interest(
                        open_interest_id, capture_id, contract_id, as_of_date,
                        open_interest, observation_state, missing_reason,
                        available_at, available_precision, source_row
                    ) VALUES (?, 'legacy-option-capture', ?, '2026-08-28', NULL,
                              'missing', 'surface_excluded_nonstandard_deliverable',
                              ?, 'datetime', ?)
                    """,
                    (f"oi-{index}", contract_id, AT, index),
                )
                connection.execute(
                    """
                    INSERT INTO option_close_prices(
                        close_price_id, capture_id, contract_id, trade_date,
                        close_price, observation_state, missing_reason,
                        available_at, available_precision, source_row
                    ) VALUES (?, 'legacy-option-capture', ?, '2026-08-28', NULL,
                              'missing', 'surface_excluded_nonstandard_deliverable',
                              ?, 'datetime', ?)
                    """,
                    (f"close-{index}", contract_id, AT, index),
                )

    def test_forward_correction_is_narrow_and_raw_relations_are_immutable(self) -> None:
        self._seed_pre_migration_rows()

        migrations = migrate_and_register_store(
            self.stores,
            self.current,
            StoreRole.MARKET,
            applied_at="2026-08-30T12:00:00-04:00",
        )
        self.assertEqual(migrations[-1], "market:0011_option_raw_evidence")

        with writer_connection(self.stores, StoreRole.MARKET) as connection:
            corrected = connection.execute(
                """
                SELECT contract.deliverable_kind, contract.nonstandard_deliverable_json,
                       surface.surface_state, surface.missing_reason,
                       surface.exclusion_reason, oi.missing_reason, close.missing_reason
                FROM option_contracts AS contract
                JOIN option_surface_snapshots AS surface USING (contract_id)
                JOIN option_open_interest AS oi USING (contract_id, capture_id)
                JOIN option_close_prices AS close USING (contract_id, capture_id)
                WHERE contract.contract_id='contract-1'
                """
            ).fetchone()
            untouched = connection.execute(
                """
                SELECT contract.deliverable_kind, surface.surface_state,
                       surface.exclusion_reason
                FROM option_contracts AS contract
                JOIN option_surface_snapshots AS surface USING (contract_id)
                WHERE contract.contract_id IN ('contract-2', 'contract-3')
                ORDER BY contract.contract_id
                """
            ).fetchall()
            self.assertEqual(
                tuple(corrected),
                (
                    "standard",
                    None,
                    "missing",
                    "source_quote_not_retained_before_raw_option_storage",
                    None,
                    "source_value_not_retained_before_raw_option_storage",
                    "source_value_not_retained_before_raw_option_storage",
                ),
            )
            self.assertEqual(
                [tuple(row) for row in untouched],
                [
                    ("nonstandard", "excluded", "nonstandard_deliverable"),
                    ("nonstandard", "excluded", "nonstandard_deliverable"),
                ],
            )
            connection.execute(
                """
                INSERT INTO option_raw_responses(
                    raw_response_id, provider, content_sha256, media_type,
                    byte_count, response_body
                ) VALUES ('raw', 'alpaca', ?, 'application/json', 2, ?)
                """,
                ("5" * 64, b"{}"),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE option_raw_responses SET media_type='text/plain'"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM option_raw_responses")

        with read_connection(self.stores, StoreRole.MARKET) as connection:
            dataset = connection.execute(
                """
                SELECT relations_json
                FROM dataset_registry
                WHERE dataset_id='market.alpaca.option_raw_evidence'
                """
            ).fetchone()
        self.assertEqual(
            json.loads(dataset[0]),
            ["option_raw_responses", "option_capture_raw_responses"],
        )


if __name__ == "__main__":
    unittest.main()
