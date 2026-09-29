"""Offline acceptance checks for the Sharadar direct identity bridge and upgrade."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from quant_data.company.sharadar_direct import (
    DEFINITION_PARAMETERS,
    contract,
    parse_definition_metadata,
    parse_definition_page,
    parse_metadata as parse_direct_metadata,
    parse_page as parse_direct_page,
    prepare_definition_snapshot,
    prepare_partition as prepare_direct_partition,
)
from quant_data.company.sharadar_definition_repository import (
    SharadarDefinitionPublisher,
    SharadarDefinitionRepository,
)
from quant_data.company.sharadar_definitions import (
    parse_definition_metadata as parse_legacy_definition_metadata,
    parse_definition_page as parse_legacy_definition_page,
    prepare_definition_snapshot as prepare_legacy_definition_snapshot,
)
from quant_data.company.sharadar_repository import SharadarSf1Publisher, SharadarSf1Repository
from quant_data.company.sharadar_sf1 import (
    parse_metadata as parse_legacy_metadata,
    parse_page as parse_legacy_page,
    prepare_partition as prepare_legacy_partition,
)
from quant_data.errors import ConflictError, ResourceLimitError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.market.collection_bindings import load_bindings, pin_binding
from quant_data.market.collection_mappings import (
    CollectionMappingPublisher,
    IdentityEvidence,
    prepare_mapping,
    unresolved_mapping,
)
from quant_data.market.collection_universe import CollectionManifestPublisher, parse_manifest
from quant_data.migrations import initialize_all, migrate_store
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, quiet_immutable_read_connection, writer_connection

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_AT = "2026-09-12T01:00:00.000000Z"
LEGACY_AT = "2026-09-12T02:00:00.000000Z"
DIRECT_AT = "2026-09-12T03:00:00.000000Z"
CORRECTION_AT = "2026-09-12T04:00:00.000000Z"
AFTER = "2026-09-12T05:00:00.000000Z"
FINAL = "2026-09-12T06:00:00.000000Z"


def description_rows() -> list[dict[str, str]]:
    rows = []
    for field in contract()["columns"]:
        name = field["direct_name"]
        rows.append({
            "table": "fundamentals",
            "indicator": name,
            "isfilter": "Y" if name in {"ticker", "dimension", "lastupdated"} else "N",
            "isprimarykey": "Y" if name in {"ticker", "dimension", "date", "reportperiod"} else "N",
            "title": name,
            "description": "Synthetic definition for " + name,
            "unittype": "text",
        })
    return rows


def description_body(rows: list[dict[str, str]] | None = None) -> bytes:
    values = description_rows() if rows is None else rows
    return dumps_strict({"count": len(values), "data": values}).encode()


def direct_schema(*, captured_at: str = SCHEMA_AT):
    return parse_direct_metadata(
        description_body(), captured_at=captured_at,
        source_reference="fixtures/sharadar-direct-descriptions.json",
    )


def native_row(**changes):
    row = {field["direct_name"]: None for field in contract()["columns"]}
    row.update({
        "ticker": "AAPL",
        "dimension": "ARQ",
        "calendardate": "2026-06-30",
        "date": "2026-07-31",
        "reportperiod": "2026-06-27",
        "fiscalperiod": "Q2",
        "lastupdated": "2026-09-08",
        "revenue": 123,
    })
    row.update(changes)
    return row


def direct_parameters(**changes) -> dict[str, str]:
    values = {
        "ticker": "AAPL",
        "dimension": "ARQ",
        "format": "json",
        "sort": "date.asc",
        "from": "1900-01-01",
        "to": "2026-12-31",
        "limit": "10000",
        "offset": "0",
    }
    values.update(changes)
    return values


def direct_page(rows=None, *, schema=None, captured_at: str = DIRECT_AT,
                parameters=None, source_reference="fixtures/sharadar-direct-fundamentals.json"):
    values = [native_row()] if rows is None else rows
    body = dumps_strict({"count": len(values), "data": values}).encode()
    return parse_direct_page(
        body, schema=schema or direct_schema(), parameters=parameters or direct_parameters(),
        captured_at=captured_at, source_reference=source_reference,
    )


def direct_partition(*pages):
    return prepare_direct_partition(
        pages, max_pages=100, max_rows=100000, max_bytes=256 * 1024 * 1024
    )


def legacy_schema():
    mapping = contract()["columns"]
    body = dumps_strict({"datatable": {
        "vendor_code": "SHARADAR",
        "datatable_code": "SF1",
        "columns": [
            {"name": field["canonical_name"], "type": field["canonical_type"]}
            for field in mapping
        ],
        "primary_key": ["ticker", "dimension", "datekey", "reportperiod"],
        "filters": ["ticker", "dimension", "datekey", "reportperiod",
                    "calendardate", "lastupdated"],
    }}).encode()
    return parse_legacy_metadata(
        body, captured_at=SCHEMA_AT,
        source_reference="fixtures/nasdaq-sf1-metadata.json",
    )


def legacy_page(*, revenue=123):
    mapping = contract()["columns"]
    native = native_row(revenue=revenue)
    body = dumps_strict({
        "datatable": {
            "columns": [
                {"name": field["canonical_name"], "type": field["canonical_type"]}
                for field in mapping
            ],
            "data": [[native[field["direct_name"]] for field in mapping]],
        },
        "meta": {"next_cursor_id": None},
    }).encode()
    return parse_legacy_page(
        body, schema=legacy_schema(),
        parameters={
            "ticker": "AAPL",
            "dimension": "ARQ",
            "qopts.per_page": "10000",
            "datekey.gte": "1900-01-01",
            "datekey.lte": "2026-12-31",
        },
        captured_at=LEGACY_AT,
        source_reference="fixtures/nasdaq-sf1-page.json",
    )


def legacy_partition():
    return prepare_legacy_partition(
        (legacy_page(),), max_pages=100, max_rows=100000,
        max_bytes=256 * 1024 * 1024,
    )


def legacy_definition_schema():
    body = dumps_strict({"datatable": {
        "vendor_code": "SHARADAR",
        "datatable_code": "INDICATORS",
        "columns": contract()["definition_columns"],
        "primary_key": ["table", "indicator"],
        "filters": ["table"],
    }}).encode()
    return parse_legacy_definition_metadata(
        body, captured_at=SCHEMA_AT,
        source_reference="fixtures/nasdaq-indicators-metadata.json",
    )


def canonical_definition_rows(*, revenue_description=None):
    rows = []
    for row in description_rows():
        canonical = {
            **row,
            "table": "SF1",
            "indicator": "datekey" if row["indicator"] == "date" else row["indicator"],
        }
        if canonical["indicator"] == "revenue" and revenue_description is not None:
            canonical["description"] = revenue_description
        rows.append(canonical)
    return rows


def legacy_definition_snapshot():
    schema = legacy_definition_schema()
    columns = contract()["definition_columns"]
    rows = canonical_definition_rows()
    body = dumps_strict({
        "datatable": {
            "columns": columns,
            "data": [[row[column["name"]] for column in columns] for row in rows],
        },
        "meta": {"next_cursor_id": None},
    }).encode()
    page = parse_legacy_definition_page(
        body, schema=schema, parameters={"table": "SF1", "qopts.per_page": "1000"},
        captured_at=LEGACY_AT, source_reference="fixtures/nasdaq-indicators-page.json",
    )
    return prepare_legacy_definition_snapshot((page,))


def direct_definition_snapshot(*, captured_at, revenue_description=None):
    rows = description_rows()
    if revenue_description is not None:
        for row in rows:
            if row["indicator"] == "revenue":
                row["description"] = revenue_description
    body = description_body(rows)
    schema = parse_definition_metadata(
        body, captured_at=captured_at,
        source_reference="fixtures/sharadar-direct-descriptions.json",
    )
    page = parse_definition_page(
        body, schema=schema, parameters=DEFINITION_PARAMETERS,
        captured_at=captured_at,
        source_reference="fixtures/sharadar-direct-descriptions.json",
    )
    return prepare_definition_snapshot((page,))


class SharadarDirectParserTests(unittest.TestCase):
    def test_full_contract_maps_date_and_preserves_canonical_identity_and_bytes(self):
        """A native date row must keep the old identity while retaining native evidence.

        Reduced legacy fixtures cannot detect drift in the complete direct inventory.
        """
        schema = direct_schema()
        direct = direct_page(schema=schema)
        legacy = legacy_page()

        self.assertEqual(len(contract()["columns"]), 112)
        self.assertEqual(schema.key_contract_id, legacy.schema.key_contract_id)
        self.assertNotEqual(schema.schema_id, legacy.schema.schema_id)
        self.assertEqual(
            direct.body, dumps_strict({"count": 1, "data": [native_row()]}).encode()
        )
        self.assertEqual(direct.rows[0].source_row_pointer, "/data/0")
        self.assertEqual(direct.rows[0].source_datekey, "2026-07-31")
        self.assertEqual(direct.rows[0].observation_id, legacy.rows[0].observation_id)
        self.assertEqual(direct.rows[0].source_key_json, legacy.rows[0].source_key_json)
        self.assertEqual(direct.rows[0].row_semantic_hash, legacy.rows[0].row_semantic_hash)
        self.assertEqual(loads_strict(direct.rows[0].values_json)["datekey"], "2026-07-31")
        self.assertNotIn("date", loads_strict(direct.rows[0].values_json))

        definitions_schema = parse_definition_metadata(
            description_body(), captured_at=SCHEMA_AT,
            source_reference="fixtures/sharadar-direct-descriptions.json",
        )
        definitions_page = parse_definition_page(
            description_body(), schema=definitions_schema,
            parameters=DEFINITION_PARAMETERS, captured_at=SCHEMA_AT,
            source_reference="fixtures/sharadar-direct-descriptions.json",
        )
        snapshot = prepare_definition_snapshot((definitions_page,))
        indicators = {row.indicator: row for row in snapshot.pages[0].rows}
        self.assertEqual(definitions_page.body, description_body())
        self.assertEqual(indicators["datekey"].source_row_pointer, "/data/3")
        self.assertEqual(loads_strict(indicators["datekey"].values_json)["table"], "SF1")

    def test_malformed_native_envelopes_inventory_and_order_fail_closed(self):
        """Malformed native payloads must fail before any partial canonical result exists.

        Legacy envelope tests do not exercise direct count, object-field, or sort rules.
        """
        schema = direct_schema()
        valid = native_row()
        with self.assertRaises(ResourceLimitError):
            parse_direct_page(
                dumps_strict({"count": 2, "data": [valid]}).encode(),
                schema=schema, parameters=direct_parameters(),
                captured_at=DIRECT_AT,
                source_reference="fixtures/count-mismatch.json",
            )
        missing = dict(valid)
        missing.pop("revenue")
        with self.assertRaises(ConflictError):
            direct_page([missing], schema=schema)

        with self.assertRaises(ConflictError):
            parse_direct_metadata(
                description_body(description_rows()[:-1]),
                captured_at=SCHEMA_AT,
                source_reference="fixtures/incomplete-descriptions.json",
            )

        reversed_dates = [
            native_row(date="2026-08-31", reportperiod="2026-07-31"),
            native_row(date="2026-07-31", reportperiod="2026-06-27"),
        ]
        with self.assertRaises(ConflictError):
            direct_page(reversed_dates, schema=schema)
        with self.assertRaises(ValidationError):
            direct_page(
                schema=schema,
                parameters={**direct_parameters(), "api_key": "must-not-enter-evidence"},
            )

    def test_offset_walk_requires_short_terminal_page_and_unique_source_keys(self):
        """Only a short final page proves completion; repeated keys invalidate the walk.

        Cursor-based Nasdaq tests cannot establish the direct offset boundary.
        """
        schema = direct_schema()
        first = direct_page(
            [native_row(date="2026-07-31", reportperiod="2026-06-27")],
            schema=schema, parameters=direct_parameters(limit="1"),
        )
        second = direct_page(
            [native_row(date="2026-08-31", reportperiod="2026-07-31")],
            schema=schema, captured_at=CORRECTION_AT,
            parameters=direct_parameters(limit="1", offset="1"),
        )
        terminal = direct_page(
            [], schema=schema, captured_at=AFTER,
            parameters=direct_parameters(limit="1", offset="2"),
        )
        self.assertTrue(direct_partition(first, second, terminal).transport_complete)
        self.assertFalse(direct_partition(first).transport_complete)
        repeated = direct_page(
            [native_row(date="2026-07-31", reportperiod="2026-06-27")],
            schema=schema, captured_at=CORRECTION_AT,
            parameters=direct_parameters(limit="1", offset="1"),
        )
        with self.assertRaises(ConflictError):
            direct_partition(first, repeated, terminal)


class SharadarDirectMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.stores = StoreMap.four_explicit(
            **{
                role: Path(self.tmp.name) / (role + ".sqlite")
                for role in ("market", "macro", "company", "news")
            }
        )
        self.current = load_registry(
            ROOT / "config/system_registry.json", project_root=ROOT, environment={}
        )
        from quant_data.registry import sharadar_direct_registry_profile

        self.previous = sharadar_direct_registry_profile(self.current)
        initialize_all(self.stores, self.previous)
        receipt = CollectionManifestPublisher(self.stores, self.previous).publish(
            parse_manifest(
                body=b"Symbol,Description\nAAPL,Apple\n",
                universe_id="major_index_liquid",
                name="Synthetic selected universe",
                source_reference="fixtures/selection.csv",
                captured_at=SCHEMA_AT,
            )
        )
        evidence = IdentityEvidence(
            b'[{"ticker":"AAPL","permaticker":"199059"}]',
            "fixtures/sharadar-tickers.json", SCHEMA_AT, "sharadar",
        )
        mapping = unresolved_mapping("AAPL")
        mapping.update(
            status="resolved", provider_symbol="AAPL", provider_subject="199059",
            evidence_sha256=evidence.sha256,
            evidence_reference=evidence.source_reference, evidence_pointer="/0",
            symbol_field="ticker", subject_field="permaticker",
            reason="Synthetic provider identity",
        )
        prepared = prepare_mapping(
            membership_snapshot_id=receipt.snapshot_id, provider="sharadar",
            captured_at=SCHEMA_AT, source_reference="fixtures/mapping.json",
            body=json.dumps([mapping]).encode(), evidence=(evidence,),
        )
        CollectionMappingPublisher(self.stores, self.previous).publish(prepared)
        binding = load_bindings(ROOT / "config/collection_bindings.json")[
            "sharadar_fundamentals"
        ]
        self.selection = pin_binding(self.stores, binding, cutoff=SCHEMA_AT)

    def _table_rows(self):
        names = (
            "company_sharadar_schema_versions",
            "company_sharadar_sf1_membership",
            "company_sharadar_definition_membership",
        )
        with quiet_immutable_read_connection(self.stores, "company") as connection:
            return {
                name: [tuple(row) for row in connection.execute("SELECT * FROM " + name)]
                for name in names
            }

    def test_upgrade_preserves_legacy_rows_and_ledger_checksums(self):
        """Applying 0017 must preserve every seeded row and prior migration checksum.

        Fresh-store tests cannot reveal destructive table-rebuild behavior.
        """
        from quant_data.registry import structured_transcript_registry_profile

        # This test isolates the historical 0017 upgrade and its exact SQL changes.
        self.current = structured_transcript_registry_profile(self.current)
        SharadarSf1Publisher(self.stores, self.previous).publish(
            legacy_partition(), selection=self.selection, ingested_at=LEGACY_AT
        )
        SharadarDefinitionPublisher(self.stores, self.previous).publish(
            legacy_definition_snapshot(), ingested_at=LEGACY_AT
        )
        before_rows = self._table_rows()
        with quiet_immutable_read_connection(self.stores, "company") as connection:
            before_ledger = [
                tuple(row)
                for row in connection.execute(
                    "SELECT migration_id,sha256 FROM schema_migrations ORDER BY ordinal"
                )
            ]
            before_triggers = {
                row["name"]: row["sql"]
                for row in connection.execute(
                    "SELECT name,sql FROM sqlite_master WHERE type='trigger'"
                )
            }

        migrate_store(self.stores, self.current, "company", applied_at=DIRECT_AT)

        self.assertEqual(before_rows, self._table_rows())
        with quiet_immutable_read_connection(self.stores, "company") as connection:
            after_ledger = [
                tuple(row)
                for row in connection.execute(
                    "SELECT migration_id,sha256 FROM schema_migrations ORDER BY ordinal"
                )
            ]
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            after_triggers = {
                row["name"]: row["sql"]
                for row in connection.execute(
                    "SELECT name,sql FROM sqlite_master WHERE type='trigger'"
                )
            }
        self.assertEqual(after_ledger[:-1], before_ledger)
        self.assertEqual(
            {name: after_triggers[name] for name in before_triggers},
            before_triggers,
        )
        self.assertEqual(
            set(after_triggers) - set(before_triggers),
            {
                "company_sharadar_direct_member_origin",
                "company_sharadar_direct_definition_origin",
            },
        )
        ledger_hashes = dict(before_ledger)
        for predecessor in self.previous.migrations_for("company"):
            self.assertEqual(ledger_hashes[predecessor.id], predecessor.sha256)
            self.assertEqual(
                hashlib.sha256((ROOT / predecessor.resource).read_bytes()).hexdigest(),
                predecessor.sha256,
            )
        declaration = next(
            item for item in self.current.migrations
            if item.id == "company:0017_sharadar_direct"
        )
        self.assertEqual(after_ledger[-1], (declaration.id, declaration.sha256))
        self.assertEqual(
            hashlib.sha256((ROOT / declaration.resource).read_bytes()).hexdigest(),
            declaration.sha256,
        )

    def test_direct_definitions_reuse_legacy_versions_then_change_as_of_state(self):
        """Equal direct definitions reuse history; a changed definition appends once.

        The migration-only check cannot prove native memberships and as-of reads coexist.
        """
        SharadarDefinitionPublisher(self.stores, self.previous).publish(
            legacy_definition_snapshot(), ingested_at=LEGACY_AT
        )
        migrate_store(self.stores, self.current, "company", applied_at=DIRECT_AT)
        publisher = SharadarDefinitionPublisher(self.stores, self.current)
        equal = direct_definition_snapshot(captured_at=DIRECT_AT)
        self.assertEqual(
            publisher.publish(equal, ingested_at=DIRECT_AT).outcome, "succeeded"
        )
        with quiet_immutable_read_connection(self.stores, "company") as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM company_sharadar_definition_versions"
                ).fetchone()[0],
                112,
            )
            pointers = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT source_row_pointer
                    FROM company_sharadar_definition_membership
                    WHERE capture_id=(
                        SELECT capture_id
                        FROM company_sharadar_definition_captures
                        ORDER BY available_at DESC LIMIT 1
                    )
                    ORDER BY source_row_index
                    """
                )
            ]
        self.assertEqual(pointers, ["/data/" + str(i) for i in range(112)])

        changed = direct_definition_snapshot(
            captured_at=CORRECTION_AT,
            revenue_description="Synthetic corrected revenue definition",
        )
        publisher.publish(changed, ingested_at=CORRECTION_AT)
        reader = SharadarDefinitionRepository(self.stores)
        prior = reader.rows(
            knowledge_cutoff=DIRECT_AT, indicator="revenue"
        )["rows"][0]
        current = reader.rows(
            knowledge_cutoff=AFTER, indicator="revenue"
        )["rows"][0]
        history = reader.rows(
            knowledge_cutoff=AFTER, indicator="revenue", mode="revision_history"
        )["rows"]
        self.assertEqual(
            prior["values"]["description"], "Synthetic definition for revenue"
        )
        self.assertEqual(
            current["values"]["description"],
            "Synthetic corrected revenue definition",
        )
        self.assertEqual([row["version_sequence"] for row in history], [1, 2])
        self.assertEqual(
            history[1]["predecessor_version_id"], history[0]["version_id"]
        )
        revenue_index = [
            field["direct_name"] for field in contract()["columns"]
        ].index("revenue")
        self.assertEqual(current["source_row_pointer"], "/data/" + str(revenue_index))

    def test_equal_direct_capture_reuses_version_then_correction_appends_and_replays(self):
        """The channel bridge reuses equal state, then appends a visible correction.

        Single-channel repository tests cannot catch a channel-only version or bad pointer.
        """
        SharadarSf1Publisher(self.stores, self.previous).publish(
            legacy_partition(), selection=self.selection, ingested_at=LEGACY_AT
        )
        migrate_store(self.stores, self.current, "company", applied_at=DIRECT_AT)
        publisher = SharadarSf1Publisher(self.stores, self.current)
        schema = direct_schema(captured_at=LEGACY_AT)
        equal = direct_partition(direct_page(schema=schema, captured_at=DIRECT_AT))
        result = publisher.publish(
            equal, selection=self.selection, ingested_at=DIRECT_AT
        )
        self.assertEqual(result.outcome, "succeeded")
        with quiet_immutable_read_connection(self.stores, "company") as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM company_sharadar_sf1_versions"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM company_sharadar_sf1_membership"
                ).fetchone()[0],
                2,
            )

        before_replay = mutation_fingerprint(self.stores)
        self.assertEqual(
            publisher.publish(
                equal, selection=self.selection, ingested_at=DIRECT_AT
            ).outcome,
            "unchanged",
        )
        self.assertEqual(before_replay, mutation_fingerprint(self.stores))

        corrected_page = direct_page(
            [native_row(revenue=124, lastupdated="2026-09-12")],
            schema=schema, captured_at=CORRECTION_AT,
            source_reference="fixtures/sharadar-direct-correction.json",
        )
        publisher.publish(
            direct_partition(corrected_page), selection=self.selection,
            ingested_at=CORRECTION_AT,
        )
        reverted_page = direct_page(
            [native_row(revenue=123, lastupdated="2026-09-13")],
            schema=schema, captured_at=AFTER,
            source_reference="fixtures/sharadar-direct-reversion.json",
        )
        publisher.publish(
            direct_partition(reverted_page), selection=self.selection,
            ingested_at=AFTER,
        )
        reader = SharadarSf1Repository(self.stores)
        before = reader.rows(
            ticker="AAPL", dimensions=("ARQ",), knowledge_cutoff=DIRECT_AT,
            mode="local_capture_as_of",
        )["rows"]
        corrected = reader.rows(
            ticker="AAPL", dimensions=("ARQ",), knowledge_cutoff=CORRECTION_AT,
            mode="local_capture_as_of",
        )["rows"]
        history = reader.rows(
            ticker="AAPL", dimensions=("ARQ",), knowledge_cutoff=FINAL,
            mode="revision_history",
        )["rows"]
        self.assertEqual(before[0]["values"]["revenue"], "123")
        self.assertEqual(corrected[0]["values"]["revenue"], "124")
        self.assertEqual(
            [row["values"]["revenue"] for row in history], ["123", "124", "123"]
        )
        self.assertEqual([row["version_sequence"] for row in history], [1, 2, 3])
        self.assertEqual(
            history[1]["predecessor_version_id"], history[0]["version_id"]
        )
        self.assertEqual(
            history[2]["predecessor_version_id"], history[1]["version_id"]
        )
        self.assertEqual(
            reader.raw_artifact(corrected_page.content_sha256), corrected_page.body
        )
        self.assertEqual(
            reader.raw_artifact(reverted_page.content_sha256), reverted_page.body
        )

        with writer_connection(self.stores, "company") as connection:
            provenance = [
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT s.normalization_version,m.source_row_pointer
                    FROM company_sharadar_sf1_membership m
                    JOIN company_sharadar_captures c ON c.capture_id=m.capture_id
                    JOIN company_sharadar_schema_versions s ON s.schema_id=c.schema_id
                    ORDER BY c.captured_at
                    """
                )
            ]
            self.assertEqual(
                provenance,
                [("nasdaq_sf1.v1", "/datatable/data/0")]
                + [("sharadar_direct.v1", "/data/0")] * 3,
            )
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "membership pointer differs from acquisition channel",
            ):
                connection.execute(
                    """
                    INSERT INTO company_sharadar_sf1_membership(
                        capture_id,page_ordinal,source_row_index,source_row_pointer,
                        observation_id,version_id,run_id
                    )
                    SELECT capture_id,page_ordinal,source_row_index+500,
                           '/datatable/data/500',observation_id,version_id,run_id
                    FROM company_sharadar_sf1_membership
                    WHERE source_row_pointer LIKE '/data/%'
                    LIMIT 1
                    """
                )


if __name__ == "__main__":
    unittest.main()
