from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from quant_data.company.sec_companyfacts import (
    AAPL_CIK,
    MAX_SUBMISSIONS_BYTES,
    SEC_COMPANYFACTS_CANONICAL_DATASET_ID,
    SEC_COMPANYFACTS_COLLECTOR_ID,
    SEC_CORE_MAPPINGS,
    SecAaplCompanyFactsPublisher,
    _domain_scope,
    parse_sec_companyfacts_bundle,
    parse_sec_aapl_bundle,
    run_sec_companyfacts,
    run_sec_aapl_companyfacts,
)
from quant_data.errors import ConflictError, ResourceLimitError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry
from quant_data.stores import (
    StoreMap,
    StoreRole,
    read_connection,
    stable_id,
    writer_connection,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
GENERIC_CIK = "0000789019"
JOINT_FIRST_CIK = "0001234567"
JOINT_SECOND_CIK = "0007654321"
JOINT_ACCESSION = "0001234567-26-000001"


def _store_map(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


def _body(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _submissions_body(*, issuer_name: str = "Apple Inc.") -> bytes:
    return _body(
        {
            "cik": "0000320193",
            "entityType": "operating",
            "name": issuer_name,
            "filings": {
                "recent": {
                    "accessionNumber": ["0000320193-26-000001"],
                    "acceptanceDateTime": ["2026-04-30T21:00:00Z"],
                    "filingDate": ["2026-04-30"],
                    "form": ["10-Q"],
                    "primaryDocument": ["xslF345X05/aapl-20260331.htm"],
                    "reportDate": ["2026-03-31"],
                }
            },
        }
    )


def _companyfacts_body(
    *,
    conflict: bool = False,
    asset_value: int = 1000,
    extra_fallback: bool = False,
) -> bytes:
    matched = "0000320193-26-000001"
    fallback = "0000320193-25-000999"
    assets = [
        {
            "accn": matched,
            "end": "2026-03-31",
            "filed": "2026-04-30",
            "form": "10-Q",
            "fp": "Q2",
            "frame": "CY2026Q1I",
            "fy": 2026,
            "val": asset_value,
        },
        {
            "accn": matched,
            "end": "2026-03-31",
            "filed": "2026-04-30",
            "form": "10-Q",
            "fp": "Q2",
            "fy": 2026,
            "val": 999,
        },
        {
            "accn": matched,
            "end": "2026-03-31",
            "filed": "2026-04-30",
            "form": "10-Q",
            "fp": "Q2",
            "val": 997,
        },
        {
            "accn": matched,
            "end": "2026-03-31",
            "filed": "2026-04-30",
            "form": "10-Q",
            "fp": None,
            "fy": 2026,
            "val": 996,
        },
    ]
    if conflict:
        assets.append(
            {
                "accn": matched,
                "end": "2026-03-31",
                "filed": "2026-04-30",
                "form": "10-Q",
                "fp": "Q2",
                "frame": "CY2026I",
                "fy": 2026,
                "val": 998,
            }
        )
    extra_facts = {}
    if extra_fallback:
        extra_facts = {
            "NetCashProvidedByUsedInOperatingActivities": {
                "units": {
                    "USD": [
                        {
                            "accn": "0000320193-24-000888",
                            "end": "2024-09-28",
                            "filed": "2024-11-01",
                            "form": "10-K",
                            "fp": "FY",
                            "frame": "CY2024",
                            "fy": 2024,
                            "start": "2023-10-01",
                            "val": 250,
                        }
                    ]
                }
            }
        }
    return _body(
        {
            "cik": 320193,
            "entityName": "Apple Inc.",
            "facts": {
                "aapl": {
                    "CustomMetric": {
                        "units": {
                            "USD": [
                                {
                                    "accn": matched,
                                    "end": "2026-03-31",
                                    "filed": "2026-04-30",
                                    "form": "10-Q",
                                    "fp": "Q2",
                                    "fy": 2026,
                                    "val": 123,
                                }
                            ]
                        }
                    }
                },
                "us-gaap": {
                    **extra_facts,
                    "Assets": {"units": {"USD": assets}},
                    "Liabilities": {
                        "units": {
                            "USD": [
                                {
                                    "accn": matched,
                                    "end": "2026-03-31",
                                    "filed": "2026-04-30",
                                    "form": "10-Q",
                                    "fp": "Q2",
                                    "fy": 2026,
                                    "segments": [{"axis": "StatementBusinessSegmentsAxis"}],
                                    "val": 500,
                                },
                                {
                                    "accn": matched,
                                    "end": "2026-03-31",
                                    "filed": "2026-04-30",
                                    "form": "8-K",
                                    "fp": "Q2",
                                    "fy": 2026,
                                    "val": 501,
                                }
                            ]
                        }
                    },
                    "NetIncomeLoss": {
                        "units": {
                            "USD": [
                                {
                                    "accn": fallback,
                                    "end": "2025-09-27",
                                    "filed": "2025-10-31",
                                    "form": "10-K",
                                    "fp": "FY",
                                    "frame": "CY2025",
                                    "fy": 2025,
                                    "start": "2024-09-29",
                                    "val": 300,
                                },
                                {
                                    "accn": fallback,
                                    "end": "2025-09-27",
                                    "filed": "2025-10-31",
                                    "form": "10-K",
                                    "fp": "Q4",
                                    "frame": "CY2025Q3",
                                    "fy": 2025,
                                    "start": "2025-06-29",
                                    "val": 80,
                                },
                            ]
                        }
                    },
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {
                        "units": {
                            "USD": [
                                {
                                    "accn": matched,
                                    "end": "2026-03-31",
                                    "filed": "2026-04-30",
                                    "form": "10-Q",
                                    "fp": "Q2",
                                    "frame": "CY2026Q1YTD",
                                    "fy": 2026,
                                    "start": "2025-10-01",
                                    "val": 200,
                                },
                                {
                                    "accn": matched,
                                    "end": "2026-03-31",
                                    "filed": "2026-04-30",
                                    "form": "10-Q",
                                    "fp": "Q2",
                                    "frame": "CY2026Q1",
                                    "fy": 2026,
                                    "start": "2026-01-01",
                                    "val": 100,
                                },
                            ]
                        }
                    },
                },
            },
        }
    )


def _generic_submissions_body(
    *,
    cik: str,
    issuer_name: str,
    accession: str,
    form: str = "10-Q",
    filing_date: str = "2026-04-30",
    report_date: str = "2026-03-31",
    acceptance_time: str = "21:00:00Z",
) -> bytes:
    return _body(
        {
            "cik": int(cik),
            "entityType": "operating",
            "name": issuer_name,
            "filings": {
                "recent": {
                    "accessionNumber": [accession],
                    "acceptanceDateTime": [f"{filing_date}T{acceptance_time}"],
                    "filingDate": [filing_date],
                    "form": [form],
                    "primaryDocument": ["reports/annual-or-quarterly.htm"],
                    "reportDate": [report_date],
                }
            },
        }
    )


def _generic_companyfacts_body(
    *,
    cik: str,
    issuer_name: str,
    accession: str,
    asset_value: int = 1000,
    form: str = "10-Q",
    fiscal_period: str = "Q2",
    fallback_accession: str | None = None,
) -> bytes:
    assets = {
        "accn": accession,
        "end": "2026-03-31",
        "filed": "2026-04-30",
        "form": form,
        "fp": fiscal_period,
        "fy": 2026,
        "val": asset_value,
    }
    if form in {"20-F", "20-F/A", "40-F", "40-F/A"}:
        assets.update(
            {
                "end": "2025-12-31",
                "filed": "2026-04-30",
                "fp": "FY",
                "fy": 2025,
                "start": "2025-01-01",
                "frame": "CY2025",
            }
        )
    else:
        assets["frame"] = "CY2026Q1I"
    facts: dict[str, object] = {"Assets": {"units": {"USD": [assets]}}}
    if fallback_accession is not None:
        facts["NetIncomeLoss"] = {
            "units": {
                "USD": [
                    {
                        "accn": fallback_accession,
                        "end": "2025-12-31",
                        "filed": "2026-02-15",
                        "form": "10-K",
                        "fp": "FY",
                        "fy": 2025,
                        "start": "2025-01-01",
                        "frame": "CY2025",
                        "val": 250,
                    }
                ]
            }
        }
    return _body(
        {
            "cik": int(cik),
            "entityName": issuer_name,
            "facts": {"us-gaap": facts},
        }
    )


class SecAaplCompanyFactsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.store_map = _store_map(Path(self._temporary.name))
        self.registry = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT)
        initialize_all(self.store_map, self.registry)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    @staticmethod
    def _capture_time(day: int = 1) -> datetime:
        return datetime(2026, 5, day, 18, 0, tzinfo=timezone.utc)

    def _parsed(self, *, conflict: bool = False, day: int = 1):
        return parse_sec_aapl_bundle(
            submissions_body=_submissions_body(),
            companyfacts_body=_companyfacts_body(conflict=conflict),
            captured_at=self._capture_time(day),
        )

    def test_publish_preserves_lineage_and_applies_context_selector(self) -> None:
        parsed = self._parsed()
        self.assertEqual(parsed.excluded_context_variant_count, 3)
        self.assertEqual(parsed.excluded_unsupported_core_row_count, 3)
        self.assertEqual(
            parsed.scope["core_row_eligibility_policy"],
            "periodic_forms_with_concrete_fy_fp_only",
        )
        self.assertEqual(len(parsed.facts), 3)

        receipt = SecAaplCompanyFactsPublisher(self.store_map, self.registry).publish(parsed)
        self.assertEqual(receipt.outcome, "succeeded")
        self.assertGreater(receipt.written_count, 0)

        with read_connection(self.store_map, StoreRole.COMPANY) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM company_issuers").fetchone()[0], 1
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM company_sec_artifacts").fetchone()[0], 2
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM company_sec_snapshots").fetchone()[0], 2
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM company_sec_filings").fetchone()[0], 2
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM company_metric_definitions").fetchone()[0],
                len(SEC_CORE_MAPPINGS),
            )
            rows = {
                row["concept"]: row
                for row in connection.execute(
                    """
                    SELECT concept, value_text, available_at, available_precision
                    FROM company_sec_fact_versions
                    """
                )
            }
            self.assertEqual(rows["Assets"]["value_text"], "1000")
            self.assertEqual(
                rows["RevenueFromContractWithCustomerExcludingAssessedTax"]["value_text"],
                "100",
            )
            self.assertEqual(rows["NetIncomeLoss"]["value_text"], "300")
            self.assertNotIn("Liabilities", rows)
            self.assertEqual(
                rows["RevenueFromContractWithCustomerExcludingAssessedTax"]["available_at"],
                "2026-04-30T21:00:00Z",
            )
            self.assertEqual(
                rows["RevenueFromContractWithCustomerExcludingAssessedTax"]["available_precision"],
                "datetime",
            )
            self.assertEqual(rows["NetIncomeLoss"]["available_at"], "2025-10-31")
            self.assertEqual(rows["NetIncomeLoss"]["available_precision"], "date")
            fallback_filing = connection.execute(
                """
                SELECT accepted_at, accepted_precision, primary_document
                FROM company_sec_filings WHERE accession_number='0000320193-25-000999'
                """
            ).fetchone()
            self.assertEqual(fallback_filing["accepted_at"], "2025-10-31")
            self.assertEqual(fallback_filing["accepted_precision"], "date")
            self.assertEqual(fallback_filing["primary_document"], "unavailable_from_companyfacts")
            warnings = connection.execute(
                "SELECT warnings_json FROM ingestion_snapshots"
            ).fetchone()[0]
            self.assertIn("companyfacts_v1_schema_policy_excludes_nonselected_context_variants", warnings)
            self.assertIn(
                "companyfacts_v1_core_policy_excludes_nonperiodic_or_incomplete_fiscal_rows",
                warnings,
            )

    def test_semantic_replay_is_a_total_no_write_even_with_a_later_capture_time(self) -> None:
        first = self._parsed(day=1)
        publisher = SecAaplCompanyFactsPublisher(self.store_map, self.registry)
        self.assertEqual(publisher.publish(first).outcome, "succeeded")
        before = mutation_fingerprint(self.store_map)

        replay = self._parsed(day=2)
        self.assertEqual(first.semantic_identity, replay.semantic_identity)
        receipt = publisher.publish(replay)
        self.assertEqual(receipt.outcome, "unchanged")
        self.assertEqual(receipt.written_count, 0)
        self.assertEqual(before, mutation_fingerprint(self.store_map))

    def test_changed_submissions_reuses_complete_companyfacts_snapshot(self) -> None:
        first = self._parsed(day=1)
        publisher = SecAaplCompanyFactsPublisher(self.store_map, self.registry)
        self.assertEqual(publisher.publish(first).outcome, "succeeded")

        changed = parse_sec_aapl_bundle(
            submissions_body=_submissions_body(issuer_name="Apple Incorporated"),
            companyfacts_body=_companyfacts_body(),
            captured_at=self._capture_time(2),
        )
        self.assertNotEqual(first.semantic_identity, changed.semantic_identity)
        self.assertEqual(publisher.publish(changed).outcome, "succeeded")

        with read_connection(self.store_map, StoreRole.COMPANY) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM company_sec_fact_versions"
                ).fetchone()[0],
                len(first.facts),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM company_fundamental_observation_versions"
                ).fetchone()[0],
                len(first.facts),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM company_sec_snapshots WHERE snapshot_kind='submissions'"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM company_sec_snapshots WHERE snapshot_kind='companyfacts'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM company_sec_fact_snapshot_membership"
                ).fetchone()[0],
                len(first.facts),
            )

    def test_changed_companyfacts_snapshot_has_complete_membership(self) -> None:
        first = self._parsed(day=1)
        publisher = SecAaplCompanyFactsPublisher(self.store_map, self.registry)
        self.assertEqual(publisher.publish(first).outcome, "succeeded")
        changed = parse_sec_aapl_bundle(
            submissions_body=_submissions_body(),
            companyfacts_body=_companyfacts_body(asset_value=1001),
            captured_at=self._capture_time(2),
        )
        self.assertEqual(publisher.publish(changed).outcome, "succeeded")

        with read_connection(self.store_map, StoreRole.COMPANY) as connection:
            memberships = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT COUNT(membership.fact_version_id)
                    FROM company_sec_snapshots AS snapshot
                    LEFT JOIN company_sec_fact_snapshot_membership AS membership
                      ON membership.snapshot_id=snapshot.snapshot_id
                    WHERE snapshot.snapshot_kind='companyfacts'
                    GROUP BY snapshot.snapshot_id
                    ORDER BY snapshot.captured_at
                    """
                )
            ]
            self.assertEqual(memberships, [len(first.facts), len(changed.facts)])
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM company_sec_fact_versions"
                ).fetchone()[0],
                len(first.facts) + len(changed.facts),
            )

    def test_new_companyfacts_fallback_gets_current_submissions_membership(self) -> None:
        first = self._parsed(day=1)
        publisher = SecAaplCompanyFactsPublisher(self.store_map, self.registry)
        self.assertEqual(publisher.publish(first).outcome, "succeeded")
        changed = parse_sec_aapl_bundle(
            submissions_body=_submissions_body(),
            companyfacts_body=_companyfacts_body(extra_fallback=True),
            captured_at=self._capture_time(2),
        )
        self.assertEqual(len(changed.filings), len(first.filings) + 1)
        self.assertEqual(publisher.publish(changed).outcome, "succeeded")

        with read_connection(self.store_map, StoreRole.COMPANY) as connection:
            submission_memberships = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT COUNT(membership.accession_number)
                    FROM company_sec_snapshots AS snapshot
                    LEFT JOIN company_sec_filing_snapshot_membership AS membership
                      ON membership.snapshot_id=snapshot.snapshot_id
                    WHERE snapshot.snapshot_kind='submissions'
                    GROUP BY snapshot.snapshot_id
                    ORDER BY snapshot.captured_at
                    """
                )
            ]
            self.assertEqual(
                submission_memberships,
                [len(first.filings), len(changed.filings)],
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM company_sec_filings"
                ).fetchone()[0],
                len(changed.filings),
            )

    def test_equal_priority_context_conflict_fails_before_any_store_mutation(self) -> None:
        before = mutation_fingerprint(self.store_map)
        with self.assertRaises(ValidationError):
            self._parsed(conflict=True)
        self.assertEqual(before, mutation_fingerprint(self.store_map))

    def test_submissions_body_bound_fails_before_parsing_or_store_access(self) -> None:
        before = mutation_fingerprint(self.store_map)
        with self.assertRaises(ResourceLimitError):
            parse_sec_aapl_bundle(
                submissions_body=b"x" * (MAX_SUBMISSIONS_BYTES + 1),
                companyfacts_body=_companyfacts_body(),
                captured_at=self._capture_time(),
            )
        self.assertEqual(before, mutation_fingerprint(self.store_map))

    def test_expired_prewrite_deadline_leaves_store_unchanged(self) -> None:
        before = mutation_fingerprint(self.store_map)
        with self.assertRaises(ResourceLimitError):
            run_sec_aapl_companyfacts(
                stores=self.store_map,
                registry=self.registry,
                submissions_body=_submissions_body(),
                companyfacts_body=_companyfacts_body(),
                captured_at=self._capture_time(),
                deadline=1.0,
            )
        self.assertEqual(before, mutation_fingerprint(self.store_map))

    def test_generic_cik_scope_source_reference_and_annual_foreign_form(self) -> None:
        accession = "0000789019-26-000001"
        submissions = _generic_submissions_body(
            cik=GENERIC_CIK,
            issuer_name="Generic Foreign Issuer plc",
            accession=accession,
            form="20-F",
            report_date="2025-12-31",
        )
        companyfacts = _generic_companyfacts_body(
            cik=GENERIC_CIK,
            issuer_name="Generic Foreign Issuer plc",
            accession=accession,
            form="20-F",
            fiscal_period="FY",
        )
        parsed = parse_sec_companyfacts_bundle(
            cik=GENERIC_CIK,
            submissions_body=submissions,
            companyfacts_body=companyfacts,
            captured_at=self._capture_time(),
        )
        self.assertEqual(parsed.cik, GENERIC_CIK)
        self.assertFalse(parsed.legacy_aapl)
        self.assertEqual(parsed.collector_id, SEC_COMPANYFACTS_COLLECTOR_ID)
        self.assertEqual(parsed.scope["cik"], GENERIC_CIK)
        self.assertEqual(len(parsed.facts), 1)
        for annual_form in ("20-F/A", "40-F", "40-F/A"):
            annual = parse_sec_companyfacts_bundle(
                cik=GENERIC_CIK,
                submissions_body=_generic_submissions_body(
                    cik=GENERIC_CIK,
                    issuer_name="Generic Foreign Issuer plc",
                    accession=accession,
                    form=annual_form,
                    report_date="2025-12-31",
                ),
                companyfacts_body=_generic_companyfacts_body(
                    cik=GENERIC_CIK,
                    issuer_name="Generic Foreign Issuer plc",
                    accession=accession,
                    form=annual_form,
                    fiscal_period="FY",
                ),
                captured_at=self._capture_time(),
            )
            self.assertEqual(len(annual.facts), 1)

        receipt = run_sec_companyfacts(
            cik=GENERIC_CIK,
            stores=self.store_map,
            registry=self.registry,
            submissions_body=submissions,
            companyfacts_body=companyfacts,
            captured_at=self._capture_time(),
        )
        self.assertEqual(receipt.outcome, "succeeded")
        with read_connection(self.store_map, StoreRole.COMPANY) as connection:
            source_references = {
                row[0]
                for row in connection.execute(
                    "SELECT source_reference FROM company_sec_artifacts"
                )
            }
            self.assertEqual(
                source_references,
                {
                    f"sec/edgar/{GENERIC_CIK}/submissions.json",
                    f"sec/edgar/{GENERIC_CIK}/companyfacts.json",
                },
            )
            self.assertEqual(
                connection.execute(
                    "SELECT command FROM ingestion_runs"
                ).fetchone()[0],
                SEC_COMPANYFACTS_COLLECTOR_ID,
            )
        with self.assertRaises(ValidationError):
            parse_sec_companyfacts_bundle(
                cik="0000000000",
                submissions_body=submissions,
                companyfacts_body=companyfacts,
                captured_at=self._capture_time(),
            )

    def test_generic_retains_endpoint_specific_filing_metadata(self) -> None:
        accession = "0000789019-26-000010"
        submissions = _generic_submissions_body(
            cik=GENERIC_CIK,
            issuer_name="Generic Issuer Inc.",
            accession=accession,
            form="10-Q/A",
            filing_date="2026-05-01",
        )
        companyfacts = _generic_companyfacts_body(
            cik=GENERIC_CIK,
            issuer_name="Generic Issuer Inc.",
            accession=accession,
            form="10-Q",
        )
        parsed = parse_sec_companyfacts_bundle(
            cik=GENERIC_CIK,
            submissions_body=submissions,
            companyfacts_body=companyfacts,
            captured_at=self._capture_time(),
        )
        self.assertEqual(parsed.filings[0].form_type, "10-Q/A")
        self.assertEqual(parsed.filings[0].filing_date, "2026-05-01")
        self.assertEqual(parsed.facts[0].form_type, "10-Q")
        self.assertEqual(parsed.facts[0].filed_at, "2026-04-30")
        self.assertEqual(parsed.facts[0].available_at.raw, "2026-05-01")
        self.assertEqual(parsed.facts[0].available_at.precision.value, "date")

    def test_generic_accepts_large_recent_filing_history(self) -> None:
        row_count = 26_000
        accessions = [
            f"{GENERIC_CIK}-26-{sequence:06d}"
            for sequence in range(1, row_count + 1)
        ]
        submissions = _body(
            {
                "cik": int(GENERIC_CIK),
                "entityType": "operating",
                "name": "Generic Issuer Inc.",
                "filings": {
                    "recent": {
                        "accessionNumber": accessions,
                        "acceptanceDateTime": ["2026-04-30T21:00:00Z"] * row_count,
                        "filingDate": ["2026-04-30"] * row_count,
                        "form": ["10-Q"] * row_count,
                        "primaryDocument": ["reports/quarterly.htm"] * row_count,
                        "reportDate": ["2026-03-31"] * row_count,
                    }
                },
            }
        )
        parsed = parse_sec_companyfacts_bundle(
            cik=GENERIC_CIK,
            submissions_body=submissions,
            companyfacts_body=_generic_companyfacts_body(
                cik=GENERIC_CIK,
                issuer_name="Generic Issuer Inc.",
                accession=accessions[0],
            ),
            captured_at=self._capture_time(),
        )
        self.assertEqual(len(parsed.filings), row_count)
        membership_scope = _domain_scope(parsed, endpoint="submissions")
        self.assertEqual(len(membership_scope["filing_membership_identity"]), 64)

    def test_corrected_generic_code_can_retry_prior_failed_run_identity(self) -> None:
        accession = "0000789019-26-000011"
        submissions = _generic_submissions_body(
            cik=GENERIC_CIK,
            issuer_name="Generic Issuer Inc.",
            accession=accession,
        )
        companyfacts = _generic_companyfacts_body(
            cik=GENERIC_CIK,
            issuer_name="Generic Issuer Inc.",
            accession=accession,
        )
        parsed = parse_sec_companyfacts_bundle(
            cik=GENERIC_CIK,
            submissions_body=submissions,
            companyfacts_body=companyfacts,
            captured_at=self._capture_time(),
        )
        prior_run_id = stable_id(
            "sec_companyfacts_run",
            SEC_COMPANYFACTS_CANONICAL_DATASET_ID,
            parsed.semantic_identity,
        )
        with writer_connection(self.store_map, StoreRole.COMPANY) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO ingestion_run_failures (
                    failure_id, run_id, dataset_id, semantic_identity, command,
                    scope_json, status, started_at, completed_at, fetched_count,
                    error_code, code_version
                ) VALUES (?, ?, ?, ?, ?, '{}', 'failed', ?, ?, ?, 'writer_error', ?)
                """,
                (
                    f"failure:{prior_run_id}",
                    prior_run_id,
                    SEC_COMPANYFACTS_CANONICAL_DATASET_ID,
                    parsed.semantic_identity,
                    SEC_COMPANYFACTS_COLLECTOR_ID,
                    parsed.captured_at,
                    parsed.captured_at,
                    parsed.fetched_count,
                    "sec_companyfacts.1.0.0",
                ),
            )
            connection.commit()
        receipt = run_sec_companyfacts(
            cik=GENERIC_CIK,
            stores=self.store_map,
            registry=self.registry,
            submissions_body=submissions,
            companyfacts_body=companyfacts,
            captured_at=self._capture_time(),
        )
        self.assertEqual(receipt.outcome, "succeeded")
        self.assertNotEqual(receipt.run_id, prior_run_id)

    def test_generic_semantic_replay_is_a_total_no_write(self) -> None:
        accession = "0000789019-26-000002"
        submissions = _generic_submissions_body(
            cik=GENERIC_CIK,
            issuer_name="Generic Issuer Inc.",
            accession=accession,
        )
        companyfacts = _generic_companyfacts_body(
            cik=GENERIC_CIK,
            issuer_name="Generic Issuer Inc.",
            accession=accession,
        )
        first = run_sec_companyfacts(
            cik=GENERIC_CIK,
            stores=self.store_map,
            registry=self.registry,
            submissions_body=submissions,
            companyfacts_body=companyfacts,
            captured_at=self._capture_time(1),
        )
        self.assertEqual(first.outcome, "succeeded")
        before = mutation_fingerprint(self.store_map)
        replay = run_sec_companyfacts(
            cik=GENERIC_CIK,
            stores=self.store_map,
            registry=self.registry,
            submissions_body=submissions,
            companyfacts_body=companyfacts,
            captured_at=self._capture_time(2),
        )
        self.assertEqual(replay.outcome, "unchanged")
        self.assertEqual(replay.written_count, 0)
        self.assertEqual(before, mutation_fingerprint(self.store_map))

    def test_generic_companyfacts_correction_appends_versions(self) -> None:
        accession = "0000789019-26-000003"
        submissions = _generic_submissions_body(
            cik=GENERIC_CIK,
            issuer_name="Generic Issuer Inc.",
            accession=accession,
        )
        first_facts = _generic_companyfacts_body(
            cik=GENERIC_CIK,
            issuer_name="Generic Issuer Inc.",
            accession=accession,
            asset_value=1000,
        )
        second_facts = _generic_companyfacts_body(
            cik=GENERIC_CIK,
            issuer_name="Generic Issuer Inc.",
            accession=accession,
            asset_value=1001,
        )
        self.assertEqual(
            run_sec_companyfacts(
                cik=GENERIC_CIK,
                stores=self.store_map,
                registry=self.registry,
                submissions_body=submissions,
                companyfacts_body=first_facts,
                captured_at=self._capture_time(1),
            ).outcome,
            "succeeded",
        )
        self.assertEqual(
            run_sec_companyfacts(
                cik=GENERIC_CIK,
                stores=self.store_map,
                registry=self.registry,
                submissions_body=submissions,
                companyfacts_body=second_facts,
                captured_at=self._capture_time(2),
            ).outcome,
            "succeeded",
        )
        with read_connection(self.store_map, StoreRole.COMPANY) as connection:
            rows = list(
                connection.execute(
                    """
                    SELECT version_sequence, value_text
                    FROM company_sec_fact_versions
                    WHERE issuer_id=? AND concept='Assets'
                    ORDER BY version_sequence
                    """,
                    (stable_id("company_issuer", GENERIC_CIK),),
                )
            )
            self.assertEqual([(row[0], row[1]) for row in rows], [(1, "1000"), (2, "1001")])

    def test_generic_companyfacts_fallback_is_issuer_scoped(self) -> None:
        accession = "0000789019-26-000004"
        fallback = "0000789019-25-000004"
        receipt = run_sec_companyfacts(
            cik=GENERIC_CIK,
            stores=self.store_map,
            registry=self.registry,
            submissions_body=_generic_submissions_body(
                cik=GENERIC_CIK,
                issuer_name="Generic Issuer Inc.",
                accession=accession,
            ),
            companyfacts_body=_generic_companyfacts_body(
                cik=GENERIC_CIK,
                issuer_name="Generic Issuer Inc.",
                accession=accession,
                fallback_accession=fallback,
            ),
            captured_at=self._capture_time(),
        )
        self.assertEqual(receipt.outcome, "succeeded")
        issuer_id = stable_id("company_issuer", GENERIC_CIK)
        with read_connection(self.store_map, StoreRole.COMPANY) as connection:
            filing = connection.execute(
                """
                SELECT source_url FROM company_sec_filings
                WHERE accession_number=?
                """,
                (fallback,),
            ).fetchone()
            self.assertEqual(
                filing[0],
                f"https://www.sec.gov/Archives/edgar/data/{int(GENERIC_CIK)}/{fallback.replace('-', '')}/",
            )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM company_sec_filing_issuer_membership
                    WHERE accession_number=? AND issuer_id=?
                    """,
                    (fallback, issuer_id),
                ).fetchone()[0],
                1,
            )

    def test_generic_joint_filing_keeps_first_observed_metadata_and_memberships(self) -> None:
        first_name = "Joint Filing Parent Inc."
        second_name = "Joint Filing Subsidiary Inc."
        first_submissions = _generic_submissions_body(
            cik=JOINT_FIRST_CIK,
            issuer_name=first_name,
            accession=JOINT_ACCESSION,
        )
        first_facts = _generic_companyfacts_body(
            cik=JOINT_FIRST_CIK,
            issuer_name=first_name,
            accession=f"{JOINT_FIRST_CIK}-26-000002",
        )
        second_submissions = _generic_submissions_body(
            cik=JOINT_SECOND_CIK,
            issuer_name=second_name,
            accession=JOINT_ACCESSION,
            acceptance_time="17:00:00Z",
        )
        second_facts = _generic_companyfacts_body(
            cik=JOINT_SECOND_CIK,
            issuer_name=second_name,
            accession=f"{JOINT_SECOND_CIK}-26-000002",
        )
        self.assertEqual(
            run_sec_companyfacts(
                cik=JOINT_FIRST_CIK,
                stores=self.store_map,
                registry=self.registry,
                submissions_body=first_submissions,
                companyfacts_body=first_facts,
                captured_at=self._capture_time(1),
            ).outcome,
            "succeeded",
        )
        self.assertEqual(
            run_sec_companyfacts(
                cik=JOINT_SECOND_CIK,
                stores=self.store_map,
                registry=self.registry,
                submissions_body=second_submissions,
                companyfacts_body=second_facts,
                captured_at=self._capture_time(2),
            ).outcome,
            "succeeded",
        )
        first_issuer_id = stable_id("company_issuer", JOINT_FIRST_CIK)
        second_issuer_id = stable_id("company_issuer", JOINT_SECOND_CIK)
        with read_connection(self.store_map, StoreRole.COMPANY) as connection:
            filing = connection.execute(
                """
                SELECT first_observed_issuer_id, source_url, accepted_at
                FROM company_sec_filings WHERE accession_number=?
                """,
                (JOINT_ACCESSION,),
            ).fetchone()
            self.assertEqual(filing[0], first_issuer_id)
            self.assertIn(f"/data/{int(JOINT_FIRST_CIK)}/", filing[1])
            self.assertEqual(filing[2], "2026-04-30T21:00:00Z")
            memberships = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT issuer_id FROM company_sec_filing_issuer_membership
                    WHERE accession_number=?
                    """,
                    (JOINT_ACCESSION,),
                )
            }
            self.assertEqual(memberships, {first_issuer_id, second_issuer_id})

        with self.assertRaises(ConflictError):
            run_sec_companyfacts(
                cik=JOINT_SECOND_CIK,
                stores=self.store_map,
                registry=self.registry,
                submissions_body=_generic_submissions_body(
                    cik=JOINT_SECOND_CIK,
                    issuer_name=second_name,
                    accession=JOINT_ACCESSION,
                    acceptance_time="22:00:00Z",
                ),
                companyfacts_body=second_facts,
                captured_at=self._capture_time(3),
            )

        with self.assertRaises(ConflictError):
            run_sec_companyfacts(
                cik=JOINT_SECOND_CIK,
                stores=self.store_map,
                registry=self.registry,
                submissions_body=_generic_submissions_body(
                    cik=JOINT_SECOND_CIK,
                    issuer_name=second_name,
                    accession=JOINT_ACCESSION,
                    form="10-K",
                ),
                companyfacts_body=_generic_companyfacts_body(
                    cik=JOINT_SECOND_CIK,
                    issuer_name=second_name,
                    accession=JOINT_ACCESSION,
                    form="10-K",
                ),
                captured_at=self._capture_time(3),
            )

    def test_generic_aapl_alias_reuses_legacy_semantic_identity(self) -> None:
        legacy = run_sec_aapl_companyfacts(
            stores=self.store_map,
            registry=self.registry,
            submissions_body=_submissions_body(),
            companyfacts_body=_companyfacts_body(),
            captured_at=self._capture_time(1),
        )
        self.assertEqual(legacy.outcome, "succeeded")
        before = mutation_fingerprint(self.store_map)
        generic_alias = run_sec_companyfacts(
            cik=AAPL_CIK,
            stores=self.store_map,
            registry=self.registry,
            submissions_body=_submissions_body(),
            companyfacts_body=_companyfacts_body(),
            captured_at=self._capture_time(2),
        )
        self.assertEqual(generic_alias.outcome, "unchanged")
        self.assertEqual(before, mutation_fingerprint(self.store_map))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
