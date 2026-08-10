from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from quant_data.company.stage4_importer import (
    CompanyStage4FixtureImporter,
    parse_stage4_company_fixture,
)
from quant_data.company.stage4_repository import CompanyStage4Query, CompanyStage4Repository
from quant_data.errors import ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.fixtures import Fixture, FixtureManifest
from quant_data.json_codec import dumps_strict
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, StoreRole, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
MANIFEST_PATH = PROJECT_ROOT / "tests" / "fixtures" / "manifest.json"
NORTHSTAR_CIK = "0001000001"
AURORA_CIK = "0001000002"


def _temporary_store_map(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


class _FixtureManifest:
    def __init__(self, fixtures: tuple[Fixture, ...]) -> None:
        self._fixtures = {fixture.id: fixture for fixture in fixtures}

    def get(self, fixture_id: str) -> Fixture:
        try:
            return self._fixtures[fixture_id]
        except KeyError as exc:
            raise ValidationError("Unknown fixture ID") from exc


class Stage4CompanyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(dir="/tmp")
        root = Path(self._temporary.name)
        self.store_map = _temporary_store_map(root)
        self.registry = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT)
        initialize_all(self.store_map, self.registry)
        self.manifest = FixtureManifest.load(MANIFEST_PATH, project_root=PROJECT_ROOT)
        self.importer = CompanyStage4FixtureImporter(self.store_map, self.manifest)
        self.repository = CompanyStage4Repository(self.store_map, self.registry)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _import(self, *fixture_ids: str) -> None:
        for fixture_id in fixture_ids:
            receipt = self.importer.import_fixture(fixture_id)
            self.assertEqual(receipt.outcome, "succeeded")

    @staticmethod
    def _query(cik: str, *, as_of: str | None = None, policy: str = "completed_date") -> CompanyStage4Query:
        return CompanyStage4Query(cik, as_of=as_of, date_only_policy=policy)

    def _mutated_fixture(
        self,
        fixture_id: str,
        *,
        source_id: str,
        mutate: Any,
        expected_semantic_identity: str | None = None,
    ) -> Fixture:
        source = self.manifest.get(source_id)
        payload = json.loads(source.bytes)
        mutate(payload)
        content = (
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        return replace(
            source,
            id=fixture_id,
            bytes=content,
            sha256=hashlib.sha256(content).hexdigest(),
            byte_count=len(content),
            expected_semantic_identity=(
                "0" * 64
                if expected_semantic_identity is None
                else expected_semantic_identity
            ),
        )


    @staticmethod
    def _replace_matching_accessions(
        payload: dict[str, Any],
        *,
        original: str,
        replacement: str,
        record_kinds: tuple[str, ...] = ("filings", "facts", "guidance"),
    ) -> int:
        replaced = 0
        for record_kind in record_kinds:
            for record in payload[record_kind]:
                if record["accession_number"] == original:
                    record["accession_number"] = replacement
                    replaced += 1
        return replaced

    def _accession_mutated_fixture(
        self,
        fixture_id: str,
        *,
        source_id: str,
        original: str,
        replacement: str,
        record_kinds: tuple[str, ...] = ("filings", "facts", "guidance"),
    ) -> Fixture:
        source = self.manifest.get(source_id)
        parsed = parse_stage4_company_fixture(source)
        semantic_payload = json.loads(dumps_strict(parsed.payload.semantic_mapping()))
        expected_replacements = self._replace_matching_accessions(
            semantic_payload,
            original=original,
            replacement=replacement,
            record_kinds=record_kinds,
        )
        self.assertGreater(expected_replacements, 0)
        semantic_material = {
            "ingestion_family_id": source.ingestion_family_id,
            "canonical_dataset_id": source.canonical_dataset_id,
            "provider": source.provider,
            "scope": dict(parsed.scope),
            "normalization_version": parsed.normalization_version,
            "payload": semantic_payload,
        }
        semantic_pin = hashlib.sha256(
            dumps_strict(semantic_material).encode("utf-8")
        ).hexdigest()

        def mutate(payload: dict[str, Any]) -> None:
            actual_replacements = self._replace_matching_accessions(
                payload,
                original=original,
                replacement=replacement,
                record_kinds=record_kinds,
            )
            self.assertEqual(actual_replacements, expected_replacements)

        return self._mutated_fixture(
            fixture_id,
            source_id=source_id,
            mutate=mutate,
            expected_semantic_identity=semantic_pin,
        )

    def test_initial_sec_state_and_exact_replay_are_nonmutating(self) -> None:
        before = mutation_fingerprint(self.store_map)
        receipt = self.importer.import_fixture("sec_initial")
        self.assertEqual(receipt.outcome, "succeeded")
        self.assertGreater(receipt.written_count, 0)
        after_initial = mutation_fingerprint(self.store_map)
        self.assertNotEqual(before["sha256"], after_initial["sha256"])

        replay = self.importer.import_fixture("sec_initial")
        self.assertEqual(replay.outcome, "unchanged")
        self.assertEqual(replay.written_count, 0)
        self.assertEqual(after_initial, mutation_fingerprint(self.store_map))

        issuer = self.repository.get_issuer(self._query(NORTHSTAR_CIK))
        self.assertEqual(issuer["cik"], NORTHSTAR_CIK)
        self.assertEqual(issuer["legal_name"], "Northstar Systems Inc.")
        self.assertTrue(
            any(link["provider_symbol"] == "NST" for link in issuer["links"])
        )

    def test_cik_identity_ticker_history_and_joint_filing_membership(self) -> None:
        self._import("sec_initial", "ticker_change", "joint_filing")

        latest = self.repository.get_issuer(self._query(NORTHSTAR_CIK))
        self.assertTrue(
            any(link["provider_symbol"] == "NSTR" for link in latest["links"])
        )
        before_rename = self.repository.get_issuer(
            self._query(NORTHSTAR_CIK, as_of="2026-03-03T12:00:00Z")
        )
        self.assertFalse(
            any(link["provider_symbol"] == "NSTR" for link in before_rename["links"])
        )
        after_rename = self.repository.get_issuer(
            self._query(NORTHSTAR_CIK, as_of="2026-03-04T00:00:00Z")
        )
        self.assertTrue(
            any(link["provider_symbol"] == "NSTR" for link in after_rename["links"])
        )

        accession = "0001000001-26-000014"
        northstar_filings = self.repository.get_filings(self._query(NORTHSTAR_CIK))
        aurora_filings = self.repository.get_filings(self._query(AURORA_CIK))
        self.assertIn(accession, {item["accession_number"] for item in northstar_filings})
        self.assertIn(accession, {item["accession_number"] for item in aurora_filings})
        with read_connection(self.store_map, StoreRole.COMPANY) as connection:
            filing_count = connection.execute(
                "SELECT COUNT(*) FROM company_sec_filings WHERE accession_number=?",
                (accession,),
            ).fetchone()[0]
            member_count = connection.execute(
                "SELECT COUNT(*) FROM company_sec_filing_issuer_membership WHERE accession_number=?",
                (accession,),
            ).fetchone()[0]
        self.assertEqual(filing_count, 1)
        self.assertEqual(member_count, 2)

    def test_companyfacts_mapping_correction_respects_as_of_and_date_precision(self) -> None:
        self._import("sec_initial", "companyfacts_correction")

        latest = self.repository.get_fundamentals(self._query(NORTHSTAR_CIK))
        total_assets = next(item for item in latest if item["metric_label"] == "Total assets")
        self.assertEqual(total_assets["value"], Decimal("1251000000"))
        self.assertEqual(total_assets["mapping_version"], "sec-core-v3")

        historical = self.repository.get_fundamentals(
            self._query(NORTHSTAR_CIK, as_of="2026-03-20T12:00:00Z")
        )
        old_assets = next(item for item in historical if item["metric_label"] == "Total assets")
        self.assertEqual(old_assets["value"], Decimal("1250000000"))

        conservative = self.repository.get_fundamentals(
            self._query(NORTHSTAR_CIK, as_of="2026-04-03T12:00:00Z")
        )
        conservative_assets = next(
            item for item in conservative if item["metric_label"] == "Total assets"
        )
        self.assertEqual(conservative_assets["value"], Decimal("1250000000"))

        inclusive = self.repository.get_fundamentals(
            self._query(
                NORTHSTAR_CIK,
                as_of="2026-04-03T12:00:00Z",
                policy="calendar_date_inclusive",
            )
        )
        inclusive_assets = next(
            item for item in inclusive if item["metric_label"] == "Total assets"
        )
        self.assertEqual(inclusive_assets["value"], Decimal("1251000000"))
        self.assertIn(
            "date_only_same_day_intraday_safety_not_established",
            inclusive_assets["warnings"],
        )

    def test_actions_and_share_semantics_remain_distinct(self) -> None:
        self._import("sec_initial", "actions_and_shares")
        fundamentals = self.repository.get_fundamentals(self._query(NORTHSTAR_CIK))
        quarter_one = [
            item
            for item in fundamentals
            if item["reference_period_end"] == "2026-03-31"
        ]
        self.assertEqual(
            {
                (item["share_semantics"], item["value"])
                for item in quarter_one
            },
            {
                ("instant", Decimal("101000000")),
                ("weighted_average", Decimal("99500000")),
            },
        )
        actions = self.repository.get_corporate_actions(self._query(NORTHSTAR_CIK))
        self.assertEqual({item["action_kind"] for item in actions}, {"cash_dividend", "split"})
        dividend = next(item for item in actions if item["action_kind"] == "cash_dividend")
        split = next(item for item in actions if item["action_kind"] == "split")
        self.assertEqual(dividend["cash_amount"], Decimal("0.25"))
        self.assertEqual(split["split_from_quantity"], Decimal("1.0"))
        self.assertEqual(split["split_to_quantity"], Decimal("2.0"))

    def test_expectation_guidance_corrections_and_event_collision_guard(self) -> None:
        self._import(
            "sec_initial",
            "actions_and_shares",
            "expectations_guidance",
            "expectations_guidance_correction",
        )
        latest = self.repository.get_earnings(self._query(NORTHSTAR_CIK))
        self.assertEqual(latest["consensus"][0]["value"], Decimal("348000000.0"))
        self.assertEqual(len(latest["events"]), 2)
        self.assertEqual(
            {item["provider_event_key"] for item in latest["events"]},
            {
                "northstar-q2-2026-preliminary",
                "northstar-q2-2026-results",
            },
        )
        guidance = latest["guidance"][0]
        self.assertEqual(guidance["low_value"], Decimal("1460000000.0"))
        self.assertEqual(
            guidance["source_url"],
            "https://example.invalid/fmp/guidance/nstr/fy2026-correction",
        )

        historical = self.repository.get_earnings(
            self._query(NORTHSTAR_CIK, as_of="2026-07-10T12:00:00Z")
        )
        self.assertEqual(historical["consensus"][0]["value"], Decimal("345000000.0"))
        self.assertEqual(
            historical["guidance"][0]["source_url"],
            "https://example.invalid/fmp/guidance/nstr/fy2026",
        )

    def test_invalid_shape_and_event_ambiguity_fail_before_any_write(self) -> None:
        malformed = self._mutated_fixture(
            "malformed_shape",
            source_id="sec_initial",
            mutate=lambda payload: payload.update({"unsupported": "field"}),
        )
        ambiguous = self._mutated_fixture(
            "ambiguous_event",
            source_id="expectations_guidance",
            mutate=lambda payload: payload["expectations"][0].update({"event_key": None}),
        )
        importer = CompanyStage4FixtureImporter(
            self.store_map,
            _FixtureManifest((malformed, ambiguous)),
        )
        before = mutation_fingerprint(self.store_map)
        with self.assertRaises(ValidationError):
            importer.import_fixture("malformed_shape")
        self.assertEqual(before, mutation_fingerprint(self.store_map))
        with self.assertRaises(ValidationError):
            importer.import_fixture("ambiguous_event")
        self.assertEqual(before, mutation_fingerprint(self.store_map))

    def test_invalid_sec_accession_fails_before_any_store_mutation(self) -> None:
        fixture = self._accession_mutated_fixture(
            "invalid_sec_accession",
            source_id="sec_initial",
            original="0001000001-26-000001",
            replacement="not-an-sec-accession",
        )
        importer = CompanyStage4FixtureImporter(
            self.store_map,
            _FixtureManifest((fixture,)),
        )
        before = mutation_fingerprint(self.store_map)

        with self.assertRaises(ValidationError) as raised:
            importer.import_fixture("invalid_sec_accession")

        self.assertEqual(raised.exception.code, "invalid_request")
        self.assertEqual(len(raised.exception.issues), 1)
        self.assertEqual(raised.exception.issues[0].pointer, "/filings/0/accession_number")
        self.assertEqual(raised.exception.issues[0].rule, "format")
        self.assertEqual(before, mutation_fingerprint(self.store_map))

    def test_malformed_sec_accession_references_fail_before_any_store_mutation(self) -> None:
        cases = (
            (
                "invalid_fact_accession_separator",
                "sec_initial",
                "0001000001-26-000001",
                "0001000001/26/000001",
                ("facts",),
                "/facts/0/accession_number",
            ),
            (
                "invalid_guidance_accession_digit_count",
                "expectations_guidance",
                "0001000001-26-000030",
                "0001000001-26-00003",
                ("guidance",),
                "/guidance/0/accession_number",
            ),
        )
        for fixture_id, source_id, original, replacement, record_kinds, pointer in cases:
            with self.subTest(fixture_id=fixture_id):
                fixture = self._accession_mutated_fixture(
                    fixture_id,
                    source_id=source_id,
                    original=original,
                    replacement=replacement,
                    record_kinds=record_kinds,
                )
                importer = CompanyStage4FixtureImporter(
                    self.store_map,
                    _FixtureManifest((fixture,)),
                )
                before = mutation_fingerprint(self.store_map)

                with self.assertRaises(ValidationError) as raised:
                    importer.import_fixture(fixture_id)

                self.assertEqual(raised.exception.code, "invalid_request")
                self.assertEqual(len(raised.exception.issues), 1)
                self.assertEqual(raised.exception.issues[0].pointer, pointer)
                self.assertEqual(raised.exception.issues[0].rule, "format")
                self.assertEqual(before, mutation_fingerprint(self.store_map))

    def test_repository_latest_and_as_of_reads_do_not_mutate_any_store(self) -> None:
        self._import(
            "sec_initial",
            "ticker_change",
            "joint_filing",
            "companyfacts_correction",
            "actions_and_shares",
            "expectations_guidance",
            "expectations_guidance_correction",
        )
        before = mutation_fingerprint(self.store_map)
        latest = self._query(NORTHSTAR_CIK)
        historical = self._query(NORTHSTAR_CIK, as_of="2026-03-20T12:00:00Z")
        self.repository.get_issuer(latest)
        self.repository.get_issuer(historical)
        self.repository.get_filings(latest)
        self.repository.get_filings(historical)
        self.repository.get_fundamentals(latest)
        self.repository.get_fundamentals(historical)
        self.repository.get_corporate_actions(latest)
        self.repository.get_corporate_actions(historical)
        self.repository.get_earnings(latest)
        self.repository.get_earnings(historical)
        self.assertEqual(before, mutation_fingerprint(self.store_map))
