"""Focused, network-free checks for the restricted Stage 9 manual wrapper."""

from __future__ import annotations

import io
import json
import socket
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from quant_data.errors import ConflictError, RegistryError, StoreUnavailableError, ValidationError
from quant_data.market import fmp_daily_prices as fmp_module
from quant_data.market.fmp_daily_prices import CapturedFmpResponse
from quant_data.migrations import initialize_all
from quant_data.operations.manual_backfill import (
    BackfillScope,
    CandidateProvenance,
    _LiveTransportContext,
    _default_fmp_bindings,
    main,
)
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry
from quant_data.stage9 import _response_bytes
from quant_data.stores import StoreMap, StoreRole, read_connection


def _store_map(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


class Stage9ManualBackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self._temporary.name)
        self.project_root = self.root / "project"
        self.project_root.mkdir()
        self.secret = "not-for-output"

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _error_streams(self) -> tuple[io.StringIO, io.StringIO]:
        return io.StringIO(), io.StringIO()

    def _assert_error(
        self,
        result: int,
        stdout: io.StringIO,
        stderr: io.StringIO,
        expected: int,
    ) -> None:
        expected_codes = {
            64: "invalid_arguments",
            69: "unavailable",
            70: "contract_failure",
            74: "local_io",
            75: "conflict",
            78: "invalid_configuration",
        }
        self.assertEqual(result, expected)
        self.assertEqual(stdout.getvalue(), "")
        lines = stderr.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(
            json.loads(lines[0]),
            {
                "contract": "quant_data.stage9_manual_backfill_error",
                "contract_version": "1.0.0",
                "error": expected_codes[expected],
                "exit_code": expected,
            },
        )
        self.assertNotIn(self.secret, stderr.getvalue())

    def _workflow_dependencies(
        self,
        events: list[str],
        target: Path,
        *,
        initializer=None,
    ) -> dict[str, object]:
        registry = object()
        response = object()
        candidate = object()
        publications: list[object] = []

        def registry_loader(*_args, **_kwargs):
            events.append("registry")
            return registry

        def registry_validator(actual) -> None:
            self.assertIs(actual, registry)
            events.append("registry_validated")

        def store_map_factory(root: Path) -> StoreMap:
            events.append("store_map")
            return _store_map(root)

        def initialize(actual_map: StoreMap, actual_registry: object) -> None:
            events.append("initialize")
            self.assertIs(actual_registry, registry)
            self.assertEqual(actual_map.path("market").parent, target / "stores")
            if initializer is not None:
                initializer(actual_map, actual_registry)

        def request_factory(scope: BackfillScope) -> object:
            events.append("request")
            self.assertEqual(scope, BackfillScope())
            return scope

        def transport_factory(api_key: str, actual_map: StoreMap) -> object:
            events.append("transport")
            self.assertEqual(api_key, self.secret)
            self.assertEqual(actual_map.path("market").parent, target / "stores")
            return object()

        def importer_factory(actual_map: StoreMap, _transport: object) -> object:
            events.append("importer")
            self.assertEqual(actual_map.path("market").parent, target / "stores")
            return object()

        def prepare(_importer: object, request: object) -> object:
            events.append("prepare")
            self.assertIsInstance(request, BackfillScope)
            return candidate

        def publish(_importer: object, prepared: object) -> dict[str, str]:
            self.assertIs(prepared, candidate)
            publications.append(prepared)
            events.append("publish_" + str(len(publications)))
            return {"outcome": "succeeded" if len(publications) == 1 else "unchanged"}

        def provenance(prepared: object, reconciliation: object) -> CandidateProvenance:
            events.append("provenance")
            self.assertIs(prepared, candidate)
            self.assertIsNotNone(reconciliation)
            return CandidateProvenance(
                request_scope=BackfillScope().request_scope(),
                response_sha256="b" * 64,
                semantic_identity="c" * 64,
                response=response,
            )

        def reconcile(actual_map: StoreMap, actual_registry: object) -> object:
            events.append("reconcile")
            self.assertEqual(actual_map.path("market").parent, target / "stores")
            self.assertIs(actual_registry, registry)
            return object()

        def backup(actual_map: StoreMap, actual_registry: object, *, target_root: Path) -> object:
            events.append("backup")
            self.assertEqual(actual_map.path("market").parent, target / "stores")
            self.assertIs(actual_registry, registry)
            self.assertEqual(target_root, target / "backup")
            return SimpleNamespace(store_map=_store_map(target_root))

        def restore(cohort: object, actual_registry: object, *, target_root: Path) -> object:
            events.append("restore_" + target_root.name)
            self.assertIs(actual_registry, registry)
            self.assertIsInstance(getattr(cohort, "store_map"), StoreMap)
            return SimpleNamespace(store_map=_store_map(target_root))

        def correction_rehearsal(
            captured_response: object,
            rehearsal_map: StoreMap,
            actual_registry: object,
        ) -> None:
            events.append("correction_rehearsal")
            self.assertIs(captured_response, response)
            self.assertEqual(rehearsal_map.path("market").parent, target / "rehearsal")
            self.assertIs(actual_registry, registry)

        def evidence_builder(
            source_map: StoreMap,
            restored_map: StoreMap,
            actual_registry: object,
            **kwargs: object,
        ) -> object:
            events.append("evidence")
            self.assertEqual(source_map.path("market").parent, target / "stores")
            self.assertEqual(restored_map.path("market").parent, target / "restored")
            self.assertIs(actual_registry, registry)
            self.assertEqual(kwargs["request_scope"], BackfillScope().request_scope())
            self.assertEqual(kwargs["response_sha256"], "b" * 64)
            self.assertEqual(kwargs["semantic_identity"], "c" * 64)
            self.assertIs(kwargs["replay_unchanged"], True)
            self.assertEqual(
                kwargs["rehearsal_map"].path("market").parent,
                target / "rehearsal",
            )
            self.assertNotIn("correction_rehearsed", kwargs)
            self.assertNotIn("correction_evidence", kwargs)
            return SimpleNamespace(sha256="d" * 64)

        def receipt_state_factory(root: Path) -> object:
            events.append("receipt_state")
            self.assertEqual(root, target / "receipts")
            return object()

        def receipt_publisher(_state: object, evidence: object, **kwargs: object) -> object:
            events.append("receipt")
            self.assertEqual(getattr(evidence, "sha256"), "d" * 64)
            self.assertEqual(kwargs["code_revision"], "a" * 64)
            self.assertEqual(kwargs["attempt_id"], "stage9-fmp-spy-202607")
            self.assertEqual(kwargs["now"](), datetime(2026, 8, 11, tzinfo=timezone.utc))
            return SimpleNamespace(receipt_sha256="e" * 64)

        return {
            "environment": {"FMP_API_KEY": self.secret},
            "registry_loader": registry_loader,
            "registry_validator": registry_validator,
            "store_map_factory": store_map_factory,
            "initializer": initialize,
            "request_factory": request_factory,
            "transport_factory": transport_factory,
            "importer_factory": importer_factory,
            "prepare": prepare,
            "publish": publish,
            "provenance": provenance,
            "reconcile": reconcile,
            "backup": backup,
            "restore": restore,
            "correction_rehearsal": correction_rehearsal,
            "evidence_builder": evidence_builder,
            "receipt_state_factory": receipt_state_factory,
            "receipt_publisher": receipt_publisher,
            "now": lambda: datetime(2026, 8, 11, tzinfo=timezone.utc),
            "code_revision": lambda _registry: "a" * 64,
            "approved_project_root": self.project_root,
            "approved_target_root": target,
        }

    def test_live_transport_context_repr_never_contains_the_key(self) -> None:
        context = _LiveTransportContext(self.secret, object())
        self.assertNotIn(self.secret, repr(context))

    def test_preflight_rejects_wrong_nonempty_alias_broad_key_and_registry_before_creation(self) -> None:
        def invoke(
            expected: Path,
            supplied: str,
            environment: dict[str, str],
            registry_loader,
        ) -> tuple[int, io.StringIO, io.StringIO, list[str]]:
            events: list[str] = []
            stdout, stderr = self._error_streams()
            result = main(
                ["--project-root", str(self.project_root), "--target-root", supplied],
                stdout=stdout,
                stderr=stderr,
                environment=environment,
                registry_loader=registry_loader,
                approved_project_root=self.project_root,
                approved_target_root=expected,
            )
            return result, stdout, stderr, events

        wrong_target = self.root / "wrong-target"
        registry_calls: list[str] = []

        def unexpected_registry(*_args, **_kwargs):
            registry_calls.append("registry")
            raise AssertionError("registry must not run")

        result, stdout, stderr, _events = invoke(
            wrong_target,
            str(wrong_target.parent),
            {"FMP_API_KEY": self.secret},
            unexpected_registry,
        )
        self._assert_error(result, stdout, stderr, 64)
        self.assertFalse(wrong_target.exists())
        self.assertEqual(registry_calls, [])

        alias_target = self.root / "alias-target"
        alias = self.root / "target-alias"
        alias.symlink_to(alias_target, target_is_directory=True)
        result, stdout, stderr, _events = invoke(
            alias_target,
            str(alias),
            {"FMP_API_KEY": self.secret},
            unexpected_registry,
        )
        self._assert_error(result, stdout, stderr, 64)
        self.assertFalse(alias_target.exists())
        self.assertEqual(registry_calls, [])

        nonempty_target = self.root / "nonempty-target"
        nonempty_target.mkdir()
        (nonempty_target / "occupied").write_text("x", encoding="utf-8")
        result, stdout, stderr, _events = invoke(
            nonempty_target,
            str(nonempty_target),
            {"FMP_API_KEY": self.secret},
            unexpected_registry,
        )
        self._assert_error(result, stdout, stderr, 75)
        self.assertEqual(registry_calls, [])

        missing_key_target = self.root / "missing-key-target"
        result, stdout, stderr, _events = invoke(
            missing_key_target,
            str(missing_key_target),
            {},
            unexpected_registry,
        )
        self._assert_error(result, stdout, stderr, 78)
        self.assertFalse(missing_key_target.exists())
        self.assertEqual(registry_calls, [])

        registry_target = self.root / "registry-target"

        def invalid_registry(*_args, **_kwargs):
            registry_calls.append("registry")
            raise RegistryError("secret must never be rendered")

        result, stdout, stderr, _events = invoke(
            registry_target,
            str(registry_target),
            {"FMP_API_KEY": self.secret},
            invalid_registry,
        )
        self._assert_error(result, stdout, stderr, 78)
        self.assertFalse(registry_target.exists())
        self.assertEqual(registry_calls, ["registry"])

    def test_provider_failure_leaves_the_fixed_target_absent(self) -> None:
        target = self.root / "provider-failure"
        events: list[str] = []
        dependencies = self._workflow_dependencies(events, target)

        def failed_prepare(_importer: object, _request: object) -> object:
            events.append("prepare_failed")
            raise StoreUnavailableError("secret provider failure")

        dependencies["prepare"] = failed_prepare
        stdout, stderr = self._error_streams()
        result = main(
            ["--project-root", str(self.project_root), "--target-root", str(target)],
            stdout=stdout,
            stderr=stderr,
            **dependencies,
        )
        self._assert_error(result, stdout, stderr, 69)
        self.assertFalse(target.exists())
        self.assertNotIn("initialize", events)
        self.assertEqual(
            events,
            [
                "registry",
                "registry_validated",
                "store_map",
                "request",
                "transport",
                "importer",
                "prepare_failed",
            ],
        )

    def test_one_primary_prepare_replay_backup_restore_rehearsal_evidence_and_receipt(self) -> None:
        target = self.root / "target"
        events: list[str] = []
        stdout, stderr = self._error_streams()
        result = main(
            ["--project-root", str(self.project_root), "--target-root", str(target)],
            stdout=stdout,
            stderr=stderr,
            **self._workflow_dependencies(events, target),
        )

        self.assertEqual(result, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(events.count("prepare"), 1)
        self.assertEqual(
            events,
            [
                "registry",
                "registry_validated",
                "store_map",
                "request",
                "transport",
                "importer",
                "prepare",
                "initialize",
                "publish_1",
                "reconcile",
                "provenance",
                "publish_2",
                "backup",
                "restore_restored",
                "restore_rehearsal",
                "correction_rehearsal",
                "evidence",
                "receipt_state",
                "receipt",
            ],
        )
        for child in ("stores", "backup", "restored", "rehearsal", "receipts"):
            self.assertTrue((target / child).is_dir())
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["replay_outcome"], "unchanged")
        self.assertTrue(payload["correction_rehearsed"])
        self.assertEqual(payload["promotion_state"], "candidate_only_no_operational_promotion")
        self.assertNotIn(self.secret, stdout.getvalue())
        self.assertNotIn(str(target), stdout.getvalue())
        source = (Path(__file__).resolve().parents[2] / "quant_data" / "operations" / "manual_backfill.py").read_text(encoding="utf-8")
        self.assertNotIn(".promote(", source)
        self.assertNotIn(".rename(", source)
        self.assertNotIn(".unlink(", source)

    def test_default_scratch_correction_path_is_network_free_and_complete(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        registry = load_registry(
            project_root / CANONICAL_REGISTRY_PATH,
            project_root=project_root,
            environment={},
        )
        approved_target = self.root / "default-correction-target"
        rehearsal = _store_map(approved_target / "stores")
        initialize_all(rehearsal, registry)
        calls: list[dict[str, object]] = []

        class OfflineLiveTransport:
            def __init__(self, store_map: StoreMap) -> None:
                self.store_map = store_map

            def _validate_stage9_live_store_map(self, store_map: StoreMap) -> object:
                if store_map is not self.store_map:
                    raise AssertionError("offline transport store binding drifted")
                return fmp_module._require_approved_live_store_map(store_map)

            def get(self, **kwargs: object) -> CapturedFmpResponse:
                calls.append(dict(kwargs))
                return CapturedFmpResponse(
                    200,
                    "application/json",
                    _response_bytes(),
                )

        with (
            mock.patch.object(
                fmp_module,
                "FMP_APPROVED_TARGET_ROOT",
                approved_target,
            ),
            mock.patch(
                "quant_data.market.fmp_daily_prices.StdlibFmpDailyPriceTransport",
                OfflineLiveTransport,
            ),
            mock.patch.object(
                socket,
                "create_connection",
                side_effect=AssertionError("network attempted"),
            ),
        ):
            bindings = _default_fmp_bindings(registry)
            transport = bindings.transport_factory(self.secret, rehearsal)
            importer = bindings.importer_factory(rehearsal, transport)
            candidate = bindings.prepare(
                importer,
                bindings.request_factory(BackfillScope()),
            )
            self.assertEqual(bindings.publish(importer, candidate).outcome, "succeeded")
            response = getattr(transport, "response", None)
            self.assertIsInstance(response, CapturedFmpResponse)
            self.assertIsNone(
                bindings.correction_rehearsal(response, rehearsal, registry)
            )

        self.assertEqual(len(calls), 1)
        with read_connection(rehearsal, StoreRole.MARKET) as connection:
            counts = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM fmp_daily_price_captures),
                    (SELECT COUNT(*) FROM fmp_daily_price_versions),
                    (SELECT MAX(correction_sequence) FROM fmp_daily_price_versions)
                """
            ).fetchone()
        self.assertIsNotNone(counts)
        self.assertEqual(tuple(counts), (2, 23, 2))

    def test_exit_mapping_is_sanitized(self) -> None:
        cases = (
            (StoreUnavailableError("secret unavailable"), 69),
            (OSError("secret io"), 74),
            (ConflictError("secret conflict"), 75),
            (ValidationError("secret contract"), 70),
        )
        for index, (failure, expected) in enumerate(cases):
            with self.subTest(expected=expected):
                target = self.root / ("failure-" + str(index))
                events: list[str] = []
                stdout, stderr = self._error_streams()

                def raising_initializer(_store_map: StoreMap, _registry: object) -> None:
                    raise failure

                result = main(
                    ["--project-root", str(self.project_root), "--target-root", str(target)],
                    stdout=stdout,
                    stderr=stderr,
                    **self._workflow_dependencies(
                        events,
                        target,
                        initializer=raising_initializer,
                    ),
                )
                self._assert_error(result, stdout, stderr, expected)
                self.assertEqual(
                    events[:8],
                    [
                        "registry",
                        "registry_validated",
                        "store_map",
                        "request",
                        "transport",
                        "importer",
                        "prepare",
                        "initialize",
                    ],
                )


if __name__ == "__main__":
    unittest.main()
