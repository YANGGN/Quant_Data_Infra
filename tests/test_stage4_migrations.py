from __future__ import annotations

from dataclasses import replace
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from quant_data.errors import MigrationError
from quant_data.migrations import _LEDGER_DDL, _apply_one, _sql_statements, initialize_all, migrate_store
from quant_data.operations.health import _reviewed_schema, inspect_all_stores
from quant_data.registry import MigrationDeclaration, load_registry, stage4_registry_profile
from quant_data.stage1 import explicit_store_map
from quant_data.stores import StoreRole, writer_connection


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
NOW = "2026-08-10T12:00:00-04:00"
STAGE4_MIGRATION_IDS = frozenset(
    {
        "market:0007_options_core",
        "market:0008_option_surface_inputs",
        "company:0003_sec_core",
        "company:0004_corporate_actions",
        "company:0005_corporate_action_integrity",
        "company:0006_earnings_expectations",
        "company:0007_filing_issuer_view",
        "news:0003_immutable_items",
        "news:0004_search_index",
    }
)


class Stage4MigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = stage4_registry_profile(
            load_registry(
                REGISTRY_PATH,
                project_root=PROJECT_ROOT,
                environment={},
            )
        )
        cls.declarations = {item.id: item for item in cls.registry.migrations}
        missing = STAGE4_MIGRATION_IDS - set(cls.declarations)
        if missing:
            raise AssertionError(f"Stage 4 migrations are absent: {sorted(missing)}")

    @staticmethod
    def _sha(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _declaration(self, migration_id: str):
        return self.declarations[migration_id]

    def _initialized(self, directory: str):
        store_map = explicit_store_map(Path(directory) / "stores")
        return store_map, initialize_all(store_map, self.registry, applied_at=NOW)

    @classmethod
    def _generic_running(
        cls,
        connection: sqlite3.Connection,
        *,
        dataset_id: str,
        suffix: str,
    ) -> tuple[str, str, str]:
        run_id = f"stage4-run-{suffix}"
        artifact_id = f"stage4-generic-artifact-{suffix}"
        snapshot_id = f"stage4-generic-snapshot-{suffix}"
        semantic_identity = cls._sha(f"run:{suffix}")
        connection.execute(
            """
            INSERT INTO ingestion_runs (
                run_id, dataset_id, semantic_identity, command, scope_json,
                status, started_at, fetched_count, written_count, warnings_json,
                code_version
            ) VALUES (?, ?, ?, 'tests.stage4_migrations', '{}', 'running',
                      ?, 1, 0, '[]', '2.2.0')
            """,
            (run_id, dataset_id, semantic_identity, NOW),
        )
        connection.execute(
            """
            INSERT INTO ingestion_run_outputs (run_id, dataset_id, semantic_identity)
            VALUES (?, ?, ?)
            """,
            (run_id, dataset_id, semantic_identity),
        )
        connection.execute(
            """
            INSERT INTO ingestion_artifacts (
                artifact_id, run_id, dataset_id, content_sha256, media_type,
                byte_count, source_reference, request_scope_json, captured_at,
                captured_precision, normalization_version
            ) VALUES (?, ?, ?, ?, 'application/json', 1, 'synthetic://stage4',
                      '{}', ?, 'datetime', 'stage4-test')
            """,
            (artifact_id, run_id, dataset_id, cls._sha(f"artifact:{suffix}"), NOW),
        )
        connection.execute(
            """
            INSERT INTO ingestion_snapshots (
                snapshot_id, run_id, dataset_id, semantic_identity, scope_json,
                completeness, row_count, captured_at, captured_precision,
                validation_state, warnings_json
            ) VALUES (?, ?, ?, ?, '{}', 'complete', 1, ?, 'datetime',
                      'validated', '[]')
            """,
            (snapshot_id, run_id, dataset_id, semantic_identity, NOW),
        )
        connection.execute(
            """
            INSERT INTO ingestion_snapshot_artifacts (
                snapshot_id, artifact_id, artifact_ordinal
            ) VALUES (?, ?, 1)
            """,
            (snapshot_id, artifact_id),
        )
        return run_id, artifact_id, snapshot_id

    @staticmethod
    def _mark_success(
        connection: sqlite3.Connection,
        candidate: tuple[str, str, str],
    ) -> None:
        run_id, artifact_id, snapshot_id = candidate
        connection.execute(
            """
            UPDATE ingestion_runs
            SET status='succeeded', completed_at=?, artifact_id=?, snapshot_id=?
            WHERE run_id=?
            """,
            (NOW, artifact_id, snapshot_id, run_id),
        )

    @staticmethod
    def _raw_apply(connection: sqlite3.Connection, registry, declarations) -> None:
        for declaration in declarations:
            sql = (registry.project_root / declaration.resource).read_text(
                encoding="utf-8"
            )
            for statement in _sql_statements(sql):
                connection.execute(statement)

    def test_real_runner_rerun_health_and_fts_schema(self) -> None:
        self.assertEqual(self.registry.revision, "2.2.0")
        with tempfile.TemporaryDirectory(prefix="quant-stage4-real-") as directory:
            store_map, first = self._initialized(directory)
            second = initialize_all(store_map, self.registry, applied_at=NOW)
            self.assertEqual(first, second)
            for role in StoreRole:
                self.assertEqual(
                    first[role.value],
                    self.registry.store(role.value).migration_order,
                )

            report = inspect_all_stores(store_map, self.registry)
            self.assertEqual(report.registry_revision, self.registry.revision)
            self.assertTrue(report.healthy)
            self.assertEqual(
                tuple(item.role for item in report.stores),
                tuple(role.value for role in StoreRole),
            )

            reviewed = _reviewed_schema(self.registry, StoreRole.NEWS)
            fts_sql = reviewed[
                ("table", "news_item_versions_fts", "news_item_versions_fts")
            ]
            self.assertTrue(fts_sql.lstrip().upper().startswith("CREATE VIRTUAL TABLE"))
            with writer_connection(store_map, StoreRole.NEWS) as connection:
                actual = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE name=?",
                    ("news_item_versions_fts",),
                ).fetchone()
                self.assertIsNotNone(actual)
                self.assertTrue(
                    str(actual[0]).lstrip().upper().startswith("CREATE VIRTUAL TABLE")
                )

        self.assertFalse((PROJECT_ROOT / "data").exists())

    def test_bytes_order_tamper_atomicity_and_fts_exception_scope(self) -> None:
        forbidden = (
            "BEGIN",
            "COMMIT",
            "ROLLBACK",
            "SAVEPOINT",
            "RELEASE",
            "ATTACH",
            "DETACH",
            "VACUUM",
            "PRAGMA",
        )
        for migration_id in STAGE4_MIGRATION_IDS:
            with self.subTest(migration_id=migration_id):
                declaration = self._declaration(migration_id)
                resource = PROJECT_ROOT / declaration.resource
                raw = resource.read_bytes()
                self.assertNotIn(b"\r", raw)
                self.assertEqual(
                    hashlib.sha256(raw).hexdigest(),
                    declaration.sha256,
                )
                statements = list(_sql_statements(raw.decode("utf-8")))
                self.assertTrue(statements)
                self.assertFalse(
                    any(
                        statement.lstrip().upper().startswith(forbidden)
                        for statement in statements
                    )
                )

        with tempfile.TemporaryDirectory(prefix="quant-stage4-order-") as directory:
            root = Path(directory)
            market = sqlite3.connect(root / "market.sqlite", isolation_level=None)
            company = sqlite3.connect(root / "company.sqlite", isolation_level=None)
            try:
                market.execute("PRAGMA foreign_keys=OFF")
                market_base = tuple(
                    item
                    for item in self.registry.migrations_for("market")
                    if item.ordinal <= 6
                )
                self._raw_apply(market, self.registry, market_base)
                with self.assertRaises(sqlite3.Error):
                    self._raw_apply(
                        market,
                        self.registry,
                        (self._declaration("market:0008_option_surface_inputs"),),
                    )

                company.execute("PRAGMA foreign_keys=OFF")
                company_base = tuple(
                    item
                    for item in self.registry.migrations_for("company")
                    if item.ordinal <= 2
                )
                self._raw_apply(company, self.registry, company_base)
                with self.assertRaises(sqlite3.Error):
                    self._raw_apply(
                        company,
                        self.registry,
                        (self._declaration("company:0007_filing_issuer_view"),),
                    )
            finally:
                market.close()
                company.close()

        tampered = replace(
            self._declaration("news:0004_search_index"),
            sha256="0" * 64,
        )
        tampered_registry = replace(
            self.registry,
            migrations=tuple(
                tampered if item.id == tampered.id else item
                for item in self.registry.migrations
            ),
        )
        with tempfile.TemporaryDirectory(prefix="quant-stage4-tamper-") as directory:
            store_map = explicit_store_map(Path(directory) / "stores")
            with self.assertRaises(MigrationError):
                migrate_store(
                    store_map,
                    tampered_registry,
                    StoreRole.NEWS,
                    applied_at=NOW,
                )
            self.assertFalse(store_map.path(StoreRole.NEWS).exists())

        partial = MigrationDeclaration(
            id="market:9999_stage4_atomic_probe",
            store="market",
            ordinal=9999,
            resource="tests/fixtures/stage4/atomic_probe.sql",
            sha256="0" * 64,
            semantic_scope="test-only atomic rollback probe",
            dependencies=(),
            reconstruction_state="unresolved",
        )
        unauthorized_fts = replace(
            self._declaration("news:0004_search_index"),
            id="news:9999_unreviewed_fts",
        )
        with tempfile.TemporaryDirectory(prefix="quant-stage4-atomic-") as directory:
            store_map = explicit_store_map(Path(directory) / "stores")
            with writer_connection(
                store_map,
                StoreRole.MARKET,
                create_parent=True,
            ) as connection:
                connection.execute(_LEDGER_DDL)
                connection.commit()
                with self.assertRaises(MigrationError):
                    _apply_one(
                        connection,
                        partial,
                        self.registry,
                        NOW,
                        "CREATE TABLE stage4_atomic_probe (id INTEGER PRIMARY KEY) STRICT; "
                        "SELECT missing_column FROM stage4_atomic_probe;",
                    )
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE name=?",
                        ("stage4_atomic_probe",),
                    ).fetchone()
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],
                    0,
                )
                with self.assertRaises(MigrationError):
                    _apply_one(
                        connection,
                        unauthorized_fts,
                        self.registry,
                        NOW,
                        "CREATE VIRTUAL TABLE stage4_unreviewed_fts "
                        "USING fts5(value, content='', tokenize=unicode61);",
                    )
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE name=?",
                        ("stage4_unreviewed_fts",),
                    ).fetchone()
                )

        self.assertFalse((PROJECT_ROOT / "data").exists())

    def _insert_market_capture(
        self,
        connection: sqlite3.Connection,
        candidate: tuple[str, str, str],
        *,
        suffix: str,
        instrument_id: str,
    ) -> str:
        run_id, artifact_id, snapshot_id = candidate
        capture_id = f"stage4-option-capture-{suffix}"
        connection.execute(
            """
            INSERT INTO option_surface_captures (
                capture_id, semantic_identity, underlying_instrument_id,
                requested_feed, resolved_feed, environment, request_scope_json,
                requested_at, requested_precision, completed_at,
                completed_precision, available_at, available_precision,
                completeness, deliverable_policy, artifact_id, source_snapshot_id,
                run_id
            ) VALUES (?, ?, ?, 'fixture', 'fixture', 'synthetic', '{}',
                      ?, 'datetime', ?, 'datetime', ?, 'datetime', 'complete',
                      'standard_only', ?, ?, ?)
            """,
            (
                capture_id,
                self._sha(f"capture:{suffix}"),
                instrument_id,
                NOW,
                NOW,
                NOW,
                artifact_id,
                snapshot_id,
                run_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO option_capture_underlyings (
                capture_id, underlying_instrument_id, underlying_state,
                missing_reason, observed_at, observed_precision
            ) VALUES (?, ?, 'present', NULL, ?, 'datetime')
            """,
            (capture_id, instrument_id, NOW),
        )
        return capture_id

    def _insert_company_sec_bundle(
        self,
        connection: sqlite3.Connection,
        candidate: tuple[str, str, str],
        *,
        suffix: str,
        issuer_id: str,
        cik: str,
    ) -> tuple[str, str]:
        run_id, _, _ = candidate
        artifact_id = f"stage4-sec-artifact-{suffix}"
        snapshot_id = f"stage4-sec-snapshot-{suffix}"
        connection.execute(
            """
            INSERT INTO company_issuers (issuer_id, cik, created_run_id)
            VALUES (?, ?, ?)
            """,
            (issuer_id, cik, run_id),
        )
        connection.execute(
            """
            INSERT INTO company_sec_artifacts (
                artifact_id, source_name, content_sha256, media_type, byte_count,
                source_reference, request_scope_json, captured_at,
                captured_precision, available_at, available_precision, run_id
            ) VALUES (?, 'sec-submissions', ?, 'application/json', 1,
                      'synthetic://sec', '{}', ?, 'datetime', ?, 'datetime', ?)
            """,
            (artifact_id, self._sha(f"sec-artifact:{suffix}"), NOW, NOW, run_id),
        )
        connection.execute(
            """
            INSERT INTO company_sec_snapshots (
                snapshot_id, semantic_identity, issuer_id, snapshot_kind,
                artifact_id, scope_json, completeness, captured_at,
                captured_precision, available_at, available_precision, run_id
            ) VALUES (?, ?, ?, 'submissions', ?, '{}', 'complete', ?, 'datetime',
                      ?, 'datetime', ?)
            """,
            (
                snapshot_id,
                self._sha(f"sec-snapshot:{suffix}"),
                issuer_id,
                artifact_id,
                NOW,
                NOW,
                run_id,
            ),
        )
        return artifact_id, snapshot_id

    def _insert_news_item_bundle(
        self,
        connection: sqlite3.Connection,
        candidate: tuple[str, str, str],
        *,
        suffix: str,
    ) -> tuple[str, str, str]:
        run_id, _, _ = candidate
        artifact_id = f"stage4-news-artifact-{suffix}"
        snapshot_id = f"stage4-news-snapshot-{suffix}"
        item_id = f"stage4-news-item-{suffix}"
        version_id = f"stage4-news-version-{suffix}"
        connection.execute(
            """
            INSERT INTO news_source_artifacts (
                artifact_id, source_name, content_sha256, media_type, byte_count,
                source_reference, request_scope_json, captured_at,
                captured_precision, available_at, available_precision, run_id
            ) VALUES (?, 'fixture-news', ?, 'application/json', 1,
                      'synthetic://news', '{}', ?, 'datetime', ?, 'datetime', ?)
            """,
            (artifact_id, self._sha(f"news-artifact:{suffix}"), NOW, NOW, run_id),
        )
        connection.execute(
            """
            INSERT INTO news_snapshots (
                snapshot_id, semantic_identity, source_name, scope_json,
                completeness, captured_at, captured_precision, available_at,
                available_precision, run_id
            ) VALUES (?, ?, 'fixture-news', '{}', 'complete', ?, 'datetime',
                      ?, 'datetime', ?)
            """,
            (snapshot_id, self._sha(f"news-snapshot:{suffix}"), NOW, NOW, run_id),
        )
        connection.execute(
            """
            INSERT INTO news_snapshot_artifacts (
                snapshot_id, artifact_id, artifact_ordinal
            ) VALUES (?, ?, 1)
            """,
            (snapshot_id, artifact_id),
        )
        connection.execute(
            """
            INSERT INTO news_items (
                item_id, source_name, source_item_id, source_kind, created_run_id
            ) VALUES (?, 'fixture-news', ?, 'article', ?)
            """,
            (item_id, f"source-{suffix}", run_id),
        )
        connection.execute(
            """
            INSERT INTO news_item_versions (
                news_item_version_id, item_id, content_identity, headline, body,
                summary, source_url, published_at, published_precision,
                content_state, content_missing_reason, item_state,
                retraction_reason, available_at, available_precision, captured_at,
                captured_precision, version_sequence,
                supersedes_news_item_version_id, source_snapshot_id, run_id,
                source_row
            ) VALUES (?, ?, ?, ?, NULL, NULL, 'synthetic://news/item',
                      ?, 'datetime', 'present', NULL, 'active', NULL, ?,
                      'datetime', ?, 'datetime', 1, NULL, ?, ?, 1)
            """,
            (
                version_id,
                item_id,
                self._sha(f"news-version:{suffix}"),
                f"Stage 4 headline {suffix}",
                NOW,
                NOW,
                NOW,
                snapshot_id,
                run_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO news_item_snapshot_membership (
                snapshot_id, news_item_version_id, run_id, source_row
            ) VALUES (?, ?, ?, 1)
            """,
            (snapshot_id, version_id, run_id),
        )
        return snapshot_id, item_id, version_id

    def test_market_capture_guards_inputs_and_post_success_append(self) -> None:
        with tempfile.TemporaryDirectory(prefix="quant-stage4-market-") as directory:
            store_map, _ = self._initialized(directory)
            with writer_connection(store_map, StoreRole.MARKET) as connection:
                connection.execute("BEGIN IMMEDIATE")
                first = self._generic_running(
                    connection,
                    dataset_id="fixture.market.options",
                    suffix="market-one",
                )
                run_one, _, _ = first
                instrument_id = "stage4-option-underlying"
                connection.execute(
                    """
                    INSERT INTO instruments (
                        instrument_id, asset_type, created_run_id
                    ) VALUES (?, 'equity', ?)
                    """,
                    (instrument_id, run_one),
                )
                capture_one = self._insert_market_capture(
                    connection,
                    first,
                    suffix="one",
                    instrument_id=instrument_id,
                )
                connection.execute(
                    """
                    INSERT INTO option_contracts (
                        contract_id, underlying_instrument_id, provider,
                        provider_contract_id, contract_symbol, expiration_date,
                        strike_price, option_type, deliverable_kind,
                        contract_multiplier, nonstandard_deliverable_json,
                        contract_status, available_at, available_precision,
                        created_run_id
                    ) VALUES (?, ?, 'fixture', 'standard-one', 'OPTSTD',
                              '2026-12-18', '100.00', 'call', 'standard', 100,
                              NULL, 'active', ?, 'datetime', ?)
                    """,
                    ("stage4-standard-contract", instrument_id, NOW, run_one),
                )
                connection.execute(
                    """
                    INSERT INTO option_contracts (
                        contract_id, underlying_instrument_id, provider,
                        provider_contract_id, contract_symbol, expiration_date,
                        strike_price, option_type, deliverable_kind,
                        contract_multiplier, nonstandard_deliverable_json,
                        contract_status, available_at, available_precision,
                        created_run_id
                    ) VALUES (?, ?, 'fixture', 'nonstandard-one', 'OPTADJ',
                              '2026-12-18', '100.00', 'call', 'nonstandard', 100,
                              '{"cash": 1}', 'active', ?, 'datetime', ?)
                    """,
                    ("stage4-nonstandard-contract", instrument_id, NOW, run_one),
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO option_surface_snapshots (
                            surface_snapshot_id, capture_id, contract_id,
                            surface_state, missing_reason, exclusion_reason,
                            available_at, available_precision, source_row
                        ) VALUES ('stage4-invalid-nonstandard', ?, ?, 'present',
                                  NULL, NULL, ?, 'datetime', 1)
                        """,
                        (capture_one, "stage4-nonstandard-contract", NOW),
                    )
                connection.execute(
                    """
                    INSERT INTO option_surface_snapshots (
                        surface_snapshot_id, capture_id, contract_id,
                        surface_state, missing_reason, exclusion_reason,
                        available_at, available_precision, source_row
                    ) VALUES ('stage4-excluded-nonstandard', ?, ?, 'excluded',
                              NULL, 'nonstandard_deliverable', ?, 'datetime', 1)
                    """,
                    (capture_one, "stage4-nonstandard-contract", NOW),
                )
                connection.execute(
                    """
                    INSERT INTO option_capture_underlying_quotes (
                        underlying_quote_id, capture_id, underlying_instrument_id,
                        input_state, missing_reason, bid_price, quote_at,
                        quote_precision, available_at, available_precision
                    ) VALUES ('stage4-quote-one', ?, ?, 'present', NULL, 100.0,
                              ?, 'datetime', ?, 'datetime')
                    """,
                    (capture_one, instrument_id, NOW, NOW),
                )
                connection.execute(
                    """
                    INSERT INTO option_capture_rate_curves (
                        rate_curve_id, capture_id, curve_date, source_name,
                        input_state, missing_reason, available_at,
                        available_precision
                    ) VALUES ('stage4-curve-one', ?, '2026-08-10', 'fixture',
                              'present', NULL, ?, 'datetime')
                    """,
                    (capture_one, NOW),
                )
                connection.execute(
                    """
                    INSERT INTO option_capture_dividend_sets (
                        dividend_set_id, capture_id, underlying_instrument_id,
                        source_name, input_state, missing_reason, available_at,
                        available_precision
                    ) VALUES ('stage4-dividend-one', ?, ?, 'fixture', 'present',
                              NULL, ?, 'datetime')
                    """,
                    (capture_one, instrument_id, NOW),
                )

                second = self._generic_running(
                    connection,
                    dataset_id="fixture.market.options",
                    suffix="market-two",
                )
                capture_two = self._insert_market_capture(
                    connection,
                    second,
                    suffix="two",
                    instrument_id=instrument_id,
                )
                connection.execute(
                    """
                    INSERT INTO option_capture_underlying_quotes (
                        underlying_quote_id, capture_id, underlying_instrument_id,
                        input_state, missing_reason, bid_price, quote_at,
                        quote_precision, available_at, available_precision
                    ) VALUES ('stage4-quote-two', ?, ?, 'present', NULL, 100.0,
                              ?, 'datetime', ?, 'datetime')
                    """,
                    (capture_two, instrument_id, NOW, NOW),
                )
                connection.execute(
                    """
                    INSERT INTO option_capture_dividend_sets (
                        dividend_set_id, capture_id, underlying_instrument_id,
                        source_name, input_state, missing_reason, available_at,
                        available_precision
                    ) VALUES ('stage4-dividend-two', ?, ?, 'fixture', 'present',
                              NULL, ?, 'datetime')
                    """,
                    (capture_two, instrument_id, NOW),
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO option_capture_expiry_inputs (
                            expiry_input_id, capture_id, expiration_date,
                            underlying_quote_id, rate_curve_id, dividend_set_id,
                            input_state, missing_reason, spot_price,
                            risk_free_rate, dividend_yield, forward_price,
                            available_at, available_precision
                        ) VALUES ('stage4-cross-capture-input', ?, '2026-12-18',
                                  'stage4-quote-two', 'stage4-curve-one',
                                  'stage4-dividend-two', 'present', NULL, 100.0,
                                  0.02, 0.01, 101.0, ?, 'datetime')
                        """,
                        (capture_two, NOW),
                    )

                self._mark_success(connection, first)
                self.assertEqual(
                    connection.execute(
                        "SELECT status FROM ingestion_runs WHERE run_id=?",
                        (run_one,),
                    ).fetchone()[0],
                    "succeeded",
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO option_contracts (
                            contract_id, underlying_instrument_id, provider,
                            provider_contract_id, contract_symbol, expiration_date,
                            strike_price, option_type, deliverable_kind,
                            contract_multiplier, nonstandard_deliverable_json,
                            contract_status, available_at, available_precision,
                            created_run_id
                        ) VALUES ('stage4-post-success-contract', ?, 'fixture',
                                  'post-success', 'OPTPOST', '2026-12-18',
                                  '100.00', 'call', 'standard', 100, NULL,
                                  'active', ?, 'datetime', ?)
                        """,
                        (instrument_id, NOW, run_one),
                    )
                self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
                connection.rollback()

        self.assertFalse((PROJECT_ROOT / "data").exists())

    def test_company_joint_filing_view_guidance_and_post_success_guards(self) -> None:
        with tempfile.TemporaryDirectory(prefix="quant-stage4-company-") as directory:
            store_map, _ = self._initialized(directory)
            with writer_connection(store_map, StoreRole.COMPANY) as connection:
                connection.execute("BEGIN IMMEDIATE")
                first = self._generic_running(
                    connection,
                    dataset_id="fixture.company.sec_evidence",
                    suffix="company-one",
                )
                run_one, _, _ = first
                _, snapshot_one = self._insert_company_sec_bundle(
                    connection,
                    first,
                    suffix="one",
                    issuer_id="stage4-issuer-one",
                    cik="0000000001",
                )
                accession = "0000000001-26-000001"
                connection.execute(
                    """
                    INSERT INTO company_sec_filings (
                        accession_number, first_observed_issuer_id, form_type,
                        filing_date, filing_date_precision, accepted_at,
                        accepted_precision, report_period_start, report_period_end,
                        primary_document, source_url, first_observed_snapshot_id,
                        available_at, available_precision, run_id
                    ) VALUES (?, 'stage4-issuer-one', '10-K', '2026-08-10', 'date',
                              ?, 'datetime', NULL, NULL, 'annual.htm',
                              'synthetic://filing', ?, ?, 'datetime', ?)
                    """,
                    (accession, NOW, snapshot_one, NOW, run_one),
                )
                connection.execute(
                    """
                    INSERT INTO company_sec_filing_snapshot_membership (
                        snapshot_id, accession_number, run_id, source_row
                    ) VALUES (?, ?, ?, 1)
                    """,
                    (snapshot_one, accession, run_one),
                )
                self._mark_success(connection, first)

                second = self._generic_running(
                    connection,
                    dataset_id="fixture.company.sec_evidence",
                    suffix="company-two",
                )
                run_two, _, _ = second
                _, snapshot_two = self._insert_company_sec_bundle(
                    connection,
                    second,
                    suffix="two",
                    issuer_id="stage4-issuer-two",
                    cik="0000000002",
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO company_sec_filings (
                            accession_number, first_observed_issuer_id, form_type,
                            filing_date, filing_date_precision, accepted_at,
                            accepted_precision, report_period_start, report_period_end,
                            primary_document, source_url, first_observed_snapshot_id,
                            available_at, available_precision, run_id
                        ) VALUES ('0000000002-26-000002', 'stage4-issuer-one',
                                  '10-K', '2026-08-10', 'date', ?, 'datetime',
                                  NULL, NULL, 'annual.htm', 'synthetic://invalid',
                                  ?, ?, 'datetime', ?)
                        """,
                        (NOW, snapshot_two, NOW, run_two),
                    )
                connection.execute(
                    """
                    INSERT INTO company_sec_filing_snapshot_membership (
                        snapshot_id, accession_number, run_id, source_row
                    ) VALUES (?, ?, ?, 1)
                    """,
                    (snapshot_two, accession, run_two),
                )
                self.assertEqual(
                    [tuple(row) for row in
                        connection.execute(
                            """
                            SELECT accession_number, issuer_id
                            FROM company_sec_filing_issuer_membership
                            WHERE accession_number=?
                            ORDER BY issuer_id
                            """,
                            (accession,),
                        )
                    ],
                    [
                        (accession, "stage4-issuer-one"),
                        (accession, "stage4-issuer-two"),
                    ],
                )
                self._mark_success(connection, second)
                filing_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(company_sec_filings)")
                }
                self.assertIn("first_observed_issuer_id", filing_columns)
                self.assertNotIn("issuer_id", filing_columns)
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
                        ("idx_company_sec_filing_membership_accession",),
                    ).fetchone()
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO company_sec_artifacts (
                            artifact_id, source_name, content_sha256, media_type,
                            byte_count, source_reference, request_scope_json,
                            captured_at, captured_precision, available_at,
                            available_precision, run_id
                        ) VALUES ('stage4-company-post-success', 'sec-submissions',
                                  ?, 'application/json', 1, 'synthetic://late',
                                  '{}', ?, 'datetime', ?, 'datetime', ?)
                        """,
                        (self._sha("late-sec-artifact"), NOW, NOW, run_one),
                    )

                third = self._generic_running(
                    connection,
                    dataset_id="fixture.company.expectation_evidence",
                    suffix="company-guidance",
                )
                run_three, _, _ = third
                connection.execute(
                    """
                    INSERT INTO company_issuers (issuer_id, cik, created_run_id)
                    VALUES ('stage4-issuer-three', '0000000003', ?)
                    """,
                    (run_three,),
                )
                connection.execute(
                    """
                    INSERT INTO company_expectation_metric_definitions (
                        expectation_metric_id, display_name, base_unit, value_kind,
                        definition_version, created_at
                    ) VALUES ('stage4-guidance-metric', 'Guidance', 'USD',
                              'currency', '1.0.0', ?)
                    """,
                    (NOW,),
                )
                connection.execute(
                    """
                    INSERT INTO company_earnings_source_artifacts (
                        artifact_id, issuer_id, provider, content_sha256, media_type,
                        byte_count, source_reference, request_scope_json, captured_at,
                        captured_precision, available_at, available_precision, run_id
                    ) VALUES ('stage4-guidance-artifact', 'stage4-issuer-three',
                              'fixture', ?, 'application/json', 1,
                              'synthetic://guidance', '{}', ?, 'datetime',
                              ?, 'datetime', ?)
                    """,
                    (self._sha("guidance-artifact"), NOW, NOW, run_three),
                )
                connection.execute(
                    """
                    INSERT INTO company_earnings_source_snapshots (
                        snapshot_id, semantic_identity, issuer_id, provider,
                        snapshot_kind, artifact_id, scope_json, completeness,
                        captured_at, captured_precision, available_at,
                        available_precision, run_id
                    ) VALUES ('stage4-guidance-snapshot', ?, 'stage4-issuer-three',
                              'fixture', 'guidance', 'stage4-guidance-artifact',
                              '{}', 'complete', ?, 'datetime', ?, 'datetime', ?)
                    """,
                    (
                        self._sha("guidance-snapshot"),
                        NOW,
                        NOW,
                        run_three,
                    ),
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO company_guidance_versions (
                            guidance_version_id, issuer_id, expectation_metric_id,
                            accession_number, source_url, target_period_start,
                            target_period_end, guidance_shape, point_value,
                            low_value, high_value, narrative, missing_reason,
                            review_state, available_at, available_precision,
                            version_sequence, supersedes_guidance_version_id,
                            source_snapshot_id, run_id, source_row
                        ) VALUES ('stage4-guidance-empty-url', 'stage4-issuer-three',
                                  'stage4-guidance-metric', NULL, '', '2026-01-01',
                                  '2026-03-31', 'point', 1.0, NULL, NULL, NULL,
                                  NULL, 'unreviewed', ?, 'datetime', 1, NULL,
                                  'stage4-guidance-snapshot', ?, 1)
                        """,
                        (NOW, run_three),
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO company_guidance_versions (
                            guidance_version_id, issuer_id, expectation_metric_id,
                            accession_number, source_url, target_period_start,
                            target_period_end, guidance_shape, point_value,
                            low_value, high_value, narrative, missing_reason,
                            review_state, available_at, available_precision,
                            version_sequence, supersedes_guidance_version_id,
                            source_snapshot_id, run_id, source_row
                        ) VALUES ('stage4-guidance-wrong-accession',
                                  'stage4-issuer-three', 'stage4-guidance-metric',
                                  ?, 'synthetic://guidance', '2026-01-01',
                                  '2026-03-31', 'point', 1.0, NULL, NULL, NULL,
                                  NULL, 'unreviewed', ?, 'datetime', 1, NULL,
                                  'stage4-guidance-snapshot', ?, 1)
                        """,
                        (accession, NOW, run_three),
                    )
                self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
                connection.rollback()

        self.assertFalse((PROJECT_ROOT / "data").exists())

    def test_news_cross_run_associations_immutability_fts_and_post_success_guard(self) -> None:
        with tempfile.TemporaryDirectory(prefix="quant-stage4-news-") as directory:
            store_map, _ = self._initialized(directory)
            with writer_connection(store_map, StoreRole.NEWS) as connection:
                connection.execute("BEGIN IMMEDIATE")
                first = self._generic_running(
                    connection,
                    dataset_id="fixture.news.evidence",
                    suffix="news-one",
                )
                run_one, _, _ = first
                snapshot_one, _, version_one = self._insert_news_item_bundle(
                    connection,
                    first,
                    suffix="one",
                )
                second = self._generic_running(
                    connection,
                    dataset_id="fixture.news.evidence",
                    suffix="news-two",
                )
                run_two, _, _ = second
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO news_item_symbols (
                            news_item_symbol_id, news_item_version_id, instrument_id,
                            provider, provider_symbol, association_state,
                            missing_reason, available_at, available_precision,
                            source_snapshot_id, run_id, source_row
                        ) VALUES ('stage4-news-cross-run', ?, 'external-instrument',
                                  'fixture', 'EXT', 'present', NULL, ?, 'datetime',
                                  ?, ?, 1)
                        """,
                        (version_one, NOW, snapshot_one, run_two),
                    )
                connection.execute(
                    """
                    INSERT INTO news_item_symbols (
                        news_item_symbol_id, news_item_version_id, instrument_id,
                        provider, provider_symbol, association_state,
                        missing_reason, available_at, available_precision,
                        source_snapshot_id, run_id, source_row
                    ) VALUES ('stage4-news-valid-symbol', ?, 'external-instrument',
                              'fixture', 'EXT', 'present', NULL, ?, 'datetime',
                              ?, ?, 1)
                    """,
                    (version_one, NOW, snapshot_one, run_one),
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        UPDATE news_item_symbols
                        SET provider='mutated'
                        WHERE news_item_symbol_id='stage4-news-valid-symbol'
                        """
                    )
                fts_match_count = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM news_item_versions_fts
                    WHERE news_item_versions_fts MATCH 'headline'
                    """
                ).fetchone()[0]
                self.assertEqual(fts_match_count, 1)

                self._mark_success(connection, first)
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO news_items (
                            item_id, source_name, source_item_id, source_kind,
                            created_run_id
                        ) VALUES ('stage4-news-post-success', 'fixture-news',
                                  'late-item', 'article', ?)
                        """,
                        (run_one,),
                    )
                self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
                connection.rollback()

        self.assertFalse((PROJECT_ROOT / "data").exists())


if __name__ == "__main__":
    unittest.main()
