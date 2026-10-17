from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from quant_data.errors import ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.market.stage12d_scope import (
    DEFAULT_STAGE12D_MARKET_NO_TRANSFER_ADOPTION_SCOPE_PATH,
    REVIEWED_STAGE12D_MARKET_NO_TRANSFER_ADOPTION_SCOPE_FILE_SHA256,
    REVIEWED_STAGE12D_MARKET_NO_TRANSFER_ADOPTION_SCOPE_SHA256,
    STAGE12D_RECEIPT_SCHEMA,
    STAGE12D_SCOPE_CONTRACT,
    STAGE12D_SCOPE_VERSION,
    load_stage12d_market_no_transfer_adoption_scope,
    require_stage12d_market_no_transfer_adoption_scope,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCOPE_PATH = PROJECT_ROOT / "config" / "stage12d_market_no_transfer_adoption_v1_scope.json"


class Stage12DNoTransferScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scope = load_stage12d_market_no_transfer_adoption_scope(SCOPE_PATH)

    def test_exact_manifest_is_pinned_and_exposes_operations_mapping(self) -> None:
        self.assertEqual(
            self.scope.manifest_sha256,
            REVIEWED_STAGE12D_MARKET_NO_TRANSFER_ADOPTION_SCOPE_SHA256,
        )
        self.assertEqual(
            self.scope.source_file_sha256,
            REVIEWED_STAGE12D_MARKET_NO_TRANSFER_ADOPTION_SCOPE_FILE_SHA256,
        )
        self.assertEqual(self.scope.contract, STAGE12D_SCOPE_CONTRACT)
        self.assertEqual(self.scope.version, STAGE12D_SCOPE_VERSION)
        self.assertEqual(self.scope.registry.revision, "2.14.0")
        self.assertEqual(self.scope.registry.schema_version, "1.8.0")
        self.assertEqual(self.scope.target.project_relative_path, "data/market.sqlite")
        self.assertEqual(
            self.scope.stage12c.completion_receipt_path,
            "data/.stage12/market-v1/stage12c-20260813-20260814/completion.json",
        )
        self.assertEqual(
            self.scope.receipt_policy.private_receipt_root,
            "data/.stage12/market-v1/stage12d-no-transfer-adoption",
        )
        self.assertEqual(self.scope.receipt_policy.receipt_schema, STAGE12D_RECEIPT_SCHEMA)
        self.assertEqual(self.scope.receipt_policy.max_proofs, 2)
        self.assertEqual(self.scope.access.connection_uri_query, "mode=ro&immutable=1")
        self.assertTrue(self.scope.access.query_only)
        self.assertEqual(self.scope.target.stamp_components, ("main", "wal", "shm", "journal"))
        self.assertEqual(self.scope.stage12e_status, "closed")
        self.assertEqual(
            self.scope.stage12c.published_complete
            + self.scope.stage12c.successful_empty
            + self.scope.stage12c.authorized_http_402,
            self.scope.stage12c.closed,
        )
        self.assertEqual(self.scope.expected_database.stage12c_current_rows, 1_238)
        self.assertEqual(self.scope.expected_database.stage12c_version_rows, 1_238)
        self.assertIs(
            require_stage12d_market_no_transfer_adoption_scope(self.scope),
            self.scope,
        )

        mapping = self.scope.manifest_mapping()
        self.assertEqual(
            hashlib.sha256(dumps_strict(mapping).encode("utf-8")).hexdigest(),
            REVIEWED_STAGE12D_MARKET_NO_TRANSFER_ADOPTION_SCOPE_SHA256,
        )
        mapping["target"]["project_relative_path"] = "data/forged.sqlite"
        self.assertEqual(self.scope.target.project_relative_path, "data/market.sqlite")
        self.assertIs(
            require_stage12d_market_no_transfer_adoption_scope(self.scope),
            self.scope,
        )

    def test_default_loader_reads_only_the_reviewed_scope_source(self) -> None:
        self.assertEqual(
            DEFAULT_STAGE12D_MARKET_NO_TRANSFER_ADOPTION_SCOPE_PATH,
            SCOPE_PATH,
        )
        with mock.patch.object(
            sqlite3,
            "connect",
            side_effect=AssertionError("scope loader must not open SQLite"),
        ):
            loaded = load_stage12d_market_no_transfer_adoption_scope()
        self.assertEqual(loaded.manifest_sha256, self.scope.manifest_sha256)

    def test_byte_equivalent_whitespace_drift_is_rejected_before_parse(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            changed = Path(temporary) / "scope.json"
            changed.write_bytes(SCOPE_PATH.read_bytes() + b"\n")
            with self.assertRaises(ValidationError):
                load_stage12d_market_no_transfer_adoption_scope(changed)

    def test_unknown_missing_type_path_policy_and_count_drift_fail_closed(self) -> None:
        source = self._source()
        source["unexpected"] = "drift"
        self._assert_parser_rejects(source)

        source = self._source()
        source.pop("stage12e")
        self._assert_parser_rejects(source)

        source = self._source()
        source["receipt_policy"]["max_proofs"] = True
        self._assert_parser_rejects(source)

        source = self._source()
        source["receipt_policy"]["max_proofs"] = 3
        self._assert_parser_rejects(source)

        source = self._source()
        source["target"]["required_link_count"] = 0
        self._assert_parser_rejects(source)

        source = self._source()
        source["target"]["project_relative_path"] = "data/other.sqlite"
        self._assert_parser_rejects(source)

        source = self._source()
        source["receipt_policy"]["private_receipt_root"] = "data/.stage12/elsewhere"
        self._assert_parser_rejects(source)

        source = self._source()
        source["access"]["normal_reader"] = True
        self._assert_parser_rejects(source)

        source = self._source()
        source["stage12c"]["ledger"]["closed"] = 628
        self._assert_parser_rejects(source)

        source = self._source()
        source["expected_database"]["stage12c_current_rows"] = 1_240
        self._assert_parser_rejects(source)

        source = self._source()
        source["credential"] = "forbidden"
        self._assert_parser_rejects(source)

    def test_forged_dataclass_and_digest_bindings_are_rejected_before_use(self) -> None:
        with self.assertRaises(ValidationError):
            require_stage12d_market_no_transfer_adoption_scope(
                replace(
                    self.scope,
                    target=replace(self.scope.target, required_link_count=2),
                )
            )
        with self.assertRaises(ValidationError):
            require_stage12d_market_no_transfer_adoption_scope(
                replace(self.scope, manifest_sha256="0" * 64)
            )
        with self.assertRaises(ValidationError):
            require_stage12d_market_no_transfer_adoption_scope(
                replace(self.scope, source_file_sha256="0" * 64)
            )
        with self.assertRaises(ValidationError):
            require_stage12d_market_no_transfer_adoption_scope(
                replace(
                    self.scope,
                    expected_database=replace(
                        self.scope.expected_database,
                        current_rows=4_237_132,
                    ),
                )
            )
        with self.assertRaises(ValidationError):
            require_stage12d_market_no_transfer_adoption_scope(object())

    def _source(self) -> dict[str, object]:
        return json.loads(SCOPE_PATH.read_text(encoding="utf-8"))

    def _assert_parser_rejects(self, source: dict[str, object]) -> None:
        payload = json.dumps(source, sort_keys=True, separators=(",", ":")).encode("utf-8")
        raw_digest = hashlib.sha256(payload).hexdigest()
        semantic_digest = hashlib.sha256(
            dumps_strict(loads_strict(payload)).encode("utf-8")
        ).hexdigest()
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            path = Path(temporary) / "scope.json"
            path.write_bytes(payload)
            with (
                mock.patch(
                    "quant_data.market.stage12d_scope.REVIEWED_STAGE12D_MARKET_NO_TRANSFER_ADOPTION_SCOPE_FILE_SHA256",
                    raw_digest,
                ),
                mock.patch(
                    "quant_data.market.stage12d_scope.REVIEWED_STAGE12D_MARKET_NO_TRANSFER_ADOPTION_SCOPE_SHA256",
                    semantic_digest,
                ),
            ):
                with self.assertRaises(ValidationError):
                    load_stage12d_market_no_transfer_adoption_scope(path)


if __name__ == "__main__":
    unittest.main()
