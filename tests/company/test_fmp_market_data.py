from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from quant_data.company.fmp_market_data import (
    FmpCompanyMarketDataPublisher,
    FmpCompanySubject,
    parse_fmp_company_response,
)
from quant_data.company.stage4_importer import CompanyStage4FixtureImporter
from quant_data.errors import ConflictError, ResourceLimitError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.fixtures import FixtureManifest
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, StoreRole, read_connection, stable_id


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
MANIFEST_PATH = PROJECT_ROOT / "tests" / "fixtures" / "manifest.json"
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "company"
NORTHSTAR_CIK = "0001000001"


def _temporary_store_map(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


class FmpCompanyMarketDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.store_map = _temporary_store_map(Path(self._temporary.name))
        self.registry = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT)
        initialize_all(self.store_map, self.registry)
        manifest = FixtureManifest.load(MANIFEST_PATH, project_root=PROJECT_ROOT)
        importer = CompanyStage4FixtureImporter(self.store_map, manifest)
        self.assertEqual(importer.import_fixture("sec_initial").outcome, "succeeded")
        self.publisher = FmpCompanyMarketDataPublisher(self.store_map, self.registry)
        self.subject = FmpCompanySubject(
            issuer_id=stable_id("company_issuer", NORTHSTAR_CIK),
            cik=NORTHSTAR_CIK,
            symbol="NST",
            instrument_id="synthetic-stage10-nst",
            identity_evidence_sha256="1" * 64,
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    @staticmethod
    def _body(name: str) -> bytes:
        return (FIXTURE_ROOT / name).read_bytes()

    def _parsed(
        self,
        source: str,
        body: bytes,
        *,
        captured_at: str = "2026-09-05T12:00:00Z",
    ):
        digest = hashlib.sha256(body).hexdigest()
        return parse_fmp_company_response(
            source=source,  # type: ignore[arg-type]
            subject=self.subject,
            body=body,
            captured_at=captured_at,
            source_reference=f"company-market-refresh/blobs/{digest}.json",
        )

    def _scalar(self, query: str, parameters: tuple[object, ...] = ()) -> object:
        with read_connection(self.store_map, StoreRole.COMPANY) as connection:
            return connection.execute(query, parameters).fetchone()[0]

    def test_payment_date_corrections_and_ticker_spelling_preserve_event_identity(self):
        from dataclasses import replace
        rows = json.loads(self._body("fmp_dividends.json"))
        self.publisher.publish(self._parsed("dividends", json.dumps(rows).encode()))
        original = self._parsed("dividends", json.dumps(rows).encode())
        original_ids = {d.provider_event_id for d in original.dividends}
        for day, field in enumerate(("recordDate", "paymentDate", "declarationDate"), 6):
            rows[0][field] = f"2026-06-{day+15:02d}"
            parsed = self._parsed("dividends", json.dumps(rows).encode(),
                                  captured_at=f"2026-09-{day:02d}T12:00:00Z")
            self.assertEqual({d.provider_event_id for d in parsed.dividends}, original_ids)
            self.publisher.publish(parsed)
        self.assertEqual(self._scalar(
            "SELECT COUNT(DISTINCT provider_event_id) FROM company_corporate_action_versions WHERE provider='fmp'"), 2)
        self.assertEqual(self._scalar(
            "SELECT MAX(version_sequence) FROM company_corporate_action_versions WHERE provider='fmp'"), 4)
        self.assertEqual(self._scalar(
            "SELECT COUNT(*) FROM company_corporate_action_versions WHERE provider='fmp' AND supersedes_action_version_id IS NOT NULL"), 3)
        renamed = replace(self.subject, symbol="NST.NEW")
        for row in rows:
            row["symbol"] = renamed.symbol
        parsed = parse_fmp_company_response(
            source="dividends", subject=renamed, body=json.dumps(rows).encode(),
            captured_at="2026-09-09T12:00:00Z", source_reference="company-market-refresh/blobs/renamed.json")
        self.assertEqual({d.provider_event_id for d in parsed.dividends}, original_ids)
        self.publisher.publish(parsed)
        self.assertEqual(self._scalar(
            "SELECT COUNT(DISTINCT provider_event_id) FROM company_corporate_action_versions WHERE provider='fmp'"), 2)

    def test_same_ex_date_ambiguity_fails_closed_and_share_classes_remain_distinct(self):
        from dataclasses import replace
        row = json.loads(self._body("fmp_dividends.json"))[0]
        for duplicate in (dict(row), {**row, "paymentDate": "2026-07-01"}):
            with self.assertRaises(ValidationError):
                self._parsed("dividends", json.dumps([row, duplicate]).encode())
        body = json.dumps([row]).encode()
        first = self._parsed("dividends", body)
        second = parse_fmp_company_response(
            source="dividends", subject=replace(self.subject, instrument_id="other-share-class"),
            body=body, captured_at="2026-09-05T12:00:00Z",
            source_reference="company-market-refresh/blobs/other.json")
        self.assertNotEqual(first.dividends[0].provider_event_id, second.dividends[0].provider_event_id)

    def test_actions_publish_raw_evidence_lineage_and_source_unspecified_cash(self) -> None:
        dividends = self._parsed("dividends", self._body("fmp_dividends.json"))
        splits = self._parsed("splits", self._body("fmp_splits.json"))

        dividend_receipt = self.publisher.publish(dividends)
        split_receipt = self.publisher.publish(splits)

        self.assertEqual(dividend_receipt.outcome, "succeeded")
        self.assertEqual(split_receipt.outcome, "succeeded")
        self.assertIn("fmp_source_currency_unspecified", dividend_receipt.warnings)
        self.assertEqual(
            self._scalar(
                "SELECT COUNT(*) FROM company_action_source_artifacts WHERE provider='fmp'"
            ),
            2,
        )
        self.assertEqual(
            self._scalar(
                "SELECT COUNT(*) FROM company_action_snapshots WHERE provider='fmp'"
            ),
            2,
        )
        self.assertEqual(
            self._scalar(
                "SELECT COUNT(*) FROM ingestion_artifacts WHERE source_reference LIKE 'company-market-refresh/blobs/%'"
            ),
            2,
        )
        self.assertEqual(
            self._scalar(
                "SELECT currency FROM company_corporate_action_versions "
                "WHERE provider='fmp' AND action_kind='cash_dividend' LIMIT 1"
            ),
            "SOURCE_UNSPECIFIED",
        )
        self.assertEqual(
            self._scalar(
                "SELECT split_from_quantity FROM company_corporate_action_versions "
                "WHERE provider='fmp' AND action_kind='split' LIMIT 1"
            ),
            1.0,
        )
        self.assertEqual(
            self._scalar(
                "SELECT split_to_quantity FROM company_corporate_action_versions "
                "WHERE provider='fmp' AND action_kind='split' LIMIT 1"
            ),
            2.0,
        )
        self.assertEqual(
            self._scalar(
                "SELECT COUNT(*) FROM company_action_snapshot_membership AS membership "
                "JOIN company_action_snapshots AS snapshot ON snapshot.snapshot_id=membership.snapshot_id "
                "WHERE snapshot.provider='fmp'"
            ),
            3,
        )

    def test_exact_semantic_replay_ignores_capture_reference_row_order_and_yield(self) -> None:
        original = self._body("fmp_dividends.json")
        first = self._parsed(original_source := "dividends", original)
        self.assertEqual(self.publisher.publish(first).outcome, "succeeded")
        before = mutation_fingerprint(self.store_map)

        reordered = json.loads(original)
        reordered.reverse()
        for row in reordered:
            row["yield"] = 99.0
        changed_raw = (json.dumps(reordered, separators=(",", ":")) + "\n").encode()
        replay = self._parsed(
            original_source,
            changed_raw,
            captured_at="2026-09-06T12:00:00Z",
        )

        self.assertEqual(first.semantic_identity, replay.semantic_identity)
        receipt = self.publisher.publish(replay)
        self.assertEqual(receipt.outcome, "unchanged")
        self.assertEqual(receipt.written_count, 0)
        self.assertEqual(before, mutation_fingerprint(self.store_map))

    def test_dividend_correction_appends_one_version_and_retains_full_snapshot_membership(self) -> None:
        self.assertEqual(
            self.publisher.publish(self._parsed("dividends", self._body("fmp_dividends.json"))).outcome,
            "succeeded",
        )
        correction = self._parsed(
            "dividends",
            self._body("fmp_dividends_correction.json"),
            captured_at="2026-09-06T12:00:00Z",
        )
        self.assertEqual(self.publisher.publish(correction).outcome, "succeeded")

        with read_connection(self.store_map, StoreRole.COMPANY) as connection:
            rows = connection.execute(
                """
                SELECT cash_amount, version_sequence, supersedes_action_version_id,
                       available_at
                FROM company_corporate_action_versions
                WHERE provider='fmp' AND action_kind='cash_dividend'
                      AND event_date='2026-06-15'
                ORDER BY version_sequence
                """
            ).fetchall()
            self.assertEqual([(row[0], row[1]) for row in rows], [(0.25, 1), (0.3, 2)])
            self.assertIsNone(rows[0][2])
            self.assertIsNotNone(rows[1][2])
            self.assertEqual(rows[0][3], "2026-09-05T12:00:00Z")
            self.assertEqual(rows[1][3], "2026-09-06T12:00:00Z")
            memberships = connection.execute(
                """
                SELECT COUNT(*)
                FROM company_action_snapshot_membership AS membership
                JOIN company_action_snapshots AS snapshot
                  ON snapshot.snapshot_id=membership.snapshot_id
                WHERE snapshot.semantic_identity=?
                """,
                (correction.semantic_identity,),
            ).fetchone()[0]
        self.assertEqual(memberships, 1)


    def test_annual_estimates_are_partial_at_limit_with_explicit_units_and_missingness(self) -> None:
        parsed = self._parsed("analyst_estimates", self._body("fmp_analyst_estimates.json"))
        self.assertEqual(parsed.raw_row_count, 10)
        self.assertEqual(parsed.completeness, "partial")
        self.assertIn("fmp_analyst_estimates_may_be_truncated_at_limit_10", parsed.warnings)

        receipt = self.publisher.publish(parsed)
        self.assertEqual(receipt.outcome, "succeeded")
        self.assertIn("fmp_source_currency_unspecified", receipt.warnings)
        self.assertEqual(
            self._scalar(
                "SELECT completeness FROM company_earnings_source_snapshots "
                "WHERE semantic_identity=?",
                (parsed.semantic_identity,),
            ),
            "partial",
        )
        self.assertEqual(
            self._scalar(
                "SELECT base_unit FROM company_expectation_metric_definitions "
                "WHERE expectation_metric_id IN ("
                "SELECT expectation_metric_id FROM company_consensus_observation_versions "
                "WHERE issuer_id=?"
                ") AND base_unit='fmp_source_currency_unspecified' LIMIT 1",
                (self.subject.issuer_id,),
            ),
            "fmp_source_currency_unspecified",
        )
        self.assertEqual(
            self._scalar(
                "SELECT base_unit FROM company_expectation_metric_definitions "
                "WHERE base_unit='fmp_source_currency_unspecified_per_share' LIMIT 1"
            ),
            "fmp_source_currency_unspecified_per_share",
        )
        self.assertEqual(
            self._scalar(
                "SELECT COUNT(*) FROM company_consensus_observation_versions "
                "WHERE issuer_id=? AND fiscal_year IS NULL AND fiscal_period IS NULL "
                "AND reference_period_start IS NULL",
                (self.subject.issuer_id,),
            ),
            81,
        )
        self.assertEqual(
            self._scalar(
                "SELECT value_state FROM company_consensus_observation_versions "
                "WHERE issuer_id=? AND value_state='missing' LIMIT 1",
                (self.subject.issuer_id,),
            ),
            "missing",
        )
        self.assertEqual(
            self._scalar(
                "SELECT missing_reason FROM company_consensus_observation_versions "
                "WHERE issuer_id=? AND value_state='missing' LIMIT 1",
                (self.subject.issuer_id,),
            ),
            "not_reported",
        )
        self.assertEqual(
            self._scalar(
                "SELECT COUNT(*) FROM company_consensus_snapshot_membership AS membership "
                "JOIN company_earnings_source_snapshots AS snapshot "
                "ON snapshot.snapshot_id=membership.snapshot_id "
                "WHERE snapshot.semantic_identity=?",
                (parsed.semantic_identity,),
            ),
            81,
        )
        self.assertEqual(
            self._scalar(
                "SELECT source_reference FROM company_earnings_source_artifacts "
                "WHERE issuer_id=? AND provider='fmp' LIMIT 1",
                (self.subject.issuer_id,),
            ).startswith("company-market-refresh/blobs/"),
            True,
        )

    def test_malformed_symbol_mismatch_and_bound_fail_before_store_write(self) -> None:
        before = mutation_fingerprint(self.store_map)
        with self.assertRaises(ValidationError):
            self._parsed("dividends", b'{"Error Message":"synthetic failure"}')
        self.assertEqual(before, mutation_fingerprint(self.store_map))

        mismatch = self._body("fmp_splits.json").replace(b'"NST"', b'"OTHER"')
        with self.assertRaises(ValidationError):
            self._parsed("splits", mismatch)
        self.assertEqual(before, mutation_fingerprint(self.store_map))

        estimates = json.loads(self._body("fmp_analyst_estimates.json"))
        estimates.append(dict(estimates[-1], date="2036-12-31"))
        with self.assertRaises(ResourceLimitError):
            self._parsed("analyst_estimates", json.dumps(estimates).encode())
        self.assertEqual(before, mutation_fingerprint(self.store_map))

    def test_empty_success_never_tombstones_existing_actions(self) -> None:
        self.assertEqual(
            self.publisher.publish(self._parsed("dividends", self._body("fmp_dividends.json"))).outcome,
            "succeeded",
        )
        before_versions = self._scalar(
            "SELECT COUNT(*) FROM company_corporate_action_versions WHERE provider='fmp'"
        )
        empty = self._parsed(
            "dividends",
            b"[]",
            captured_at="2026-09-06T12:00:00Z",
        )
        receipt = self.publisher.publish(empty)

        self.assertEqual(receipt.outcome, "succeeded")
        self.assertIn("fmp_empty_success_response_no_tombstone", receipt.warnings)
        self.assertEqual(
            before_versions,
            self._scalar(
                "SELECT COUNT(*) FROM company_corporate_action_versions WHERE provider='fmp'"
            ),
        )
        self.assertEqual(
            self._scalar(
                "SELECT COUNT(*) FROM company_corporate_action_versions "
                "WHERE provider='fmp' AND action_state='tombstone'"
            ),
            0,
        )
        self.assertEqual(
            self._scalar(
                "SELECT tombstone_authoritative FROM company_action_snapshots "
                "WHERE semantic_identity=?",
                (empty.semantic_identity,),
            ),
            0,
        )

    def test_unknown_existing_issuer_fails_without_creating_identity_or_actions(self) -> None:
        unknown = FmpCompanySubject(
            issuer_id="unknown-issuer",
            cik="0001999999",
            symbol="NST",
            instrument_id="synthetic-stage10-nst",
            identity_evidence_sha256="2" * 64,
        )
        body = self._body("fmp_splits.json")
        digest = hashlib.sha256(body).hexdigest()
        parsed = parse_fmp_company_response(
            source="splits",
            subject=unknown,
            body=body,
            captured_at="2026-09-05T12:00:00Z",
            source_reference=f"company-market-refresh/blobs/{digest}.json",
        )
        with self.assertRaises(ConflictError):
            self.publisher.publish(parsed)
        self.assertEqual(
            self._scalar("SELECT COUNT(*) FROM company_issuers WHERE cik='0001999999'"),
            0,
        )
        self.assertEqual(
            self._scalar(
                "SELECT COUNT(*) FROM company_corporate_action_versions "
                "WHERE issuer_id='unknown-issuer'"
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
