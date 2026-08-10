"""Pure semantic identity coverage for the offline Stage 3 macro fixtures."""

from __future__ import annotations

import copy
import re
import unittest
from pathlib import Path

from quant_data.errors import ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.macro.stage3_normalizers import (
    parse_stage3_macro_fixture,
    semantic_identity_eia_weekly,
)


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "stage3" / "macro"


def _document(name: str) -> dict[str, object]:
    raw = loads_strict((FIXTURE_ROOT / f"{name}.json").read_bytes())
    assert isinstance(raw, dict)
    return raw


def _candidate(name: str):
    return parse_stage3_macro_fixture(name, (FIXTURE_ROOT / f"{name}.json").read_bytes())


def _candidate_from_document(name: str, document: dict[str, object]):
    return parse_stage3_macro_fixture(name, dumps_strict(document).encode("utf-8"))


class Stage3MacroSemanticIdentityTests(unittest.TestCase):
    def test_bls_excludes_only_top_level_response_time(self) -> None:
        base = _candidate("bls_base")
        volatile_only = _candidate("bls_response_time_only")
        self.assertEqual(base.semantic_identity, volatile_only.semantic_identity)

        message_changed = _document("bls_base")
        payload = message_changed["payload"]
        assert isinstance(payload, dict)
        response = payload["response"]
        assert isinstance(response, dict)
        response["message"] = ["material provider message"]
        self.assertNotEqual(
            base.semantic_identity,
            _candidate_from_document("bls_message_changed", message_changed).semantic_identity,
        )

        observation_changed = _document("bls_base")
        payload = observation_changed["payload"]
        assert isinstance(payload, dict)
        observations = payload["observations"]
        assert isinstance(observations, list)
        assert isinstance(observations[0], dict)
        observations[0]["value"] = "320.2"
        self.assertNotEqual(
            base.semantic_identity,
            _candidate_from_document("bls_observation_changed", observation_changed).semantic_identity,
        )

    def test_bea_excludes_only_parsed_table_utc_production_time(self) -> None:
        base = _candidate("bea_base")
        volatile_only = _candidate("bea_utc_only")
        self.assertEqual(base.semantic_identity, volatile_only.semantic_identity)

        table_changed = _document("bea_base")
        payload = table_changed["payload"]
        assert isinstance(payload, dict)
        parsed = payload["parsed_table"]
        assert isinstance(parsed, dict)
        parsed["TableName"] = "Changed table"
        self.assertNotEqual(
            base.semantic_identity,
            _candidate_from_document("bea_table_changed", table_changed).semantic_identity,
        )

        observation_changed = _document("bea_base")
        payload = observation_changed["payload"]
        assert isinstance(payload, dict)
        observations = payload["observations"]
        assert isinstance(observations, list)
        assert isinstance(observations[0], dict)
        observations[0]["value"] = "101.0"
        self.assertNotEqual(
            base.semantic_identity,
            _candidate_from_document("bea_observation_changed", observation_changed).semantic_identity,
        )

    def test_soma_is_summary_only_and_rejects_security_shaped_material(self) -> None:
        base = _candidate("soma_summary")
        self.assertEqual(base.family, "soma")
        self.assertTrue(base.request_scope["summary_only"])

        forbidden_key = _document("soma_summary")
        payload = forbidden_key["payload"]
        assert isinstance(payload, dict)
        latest = payload["latest_summary_release"]
        assert isinstance(latest, dict)
        latest["CU" + "SIP"] = "123456789"
        with self.assertRaises(ValidationError):
            _candidate_from_document("soma_forbidden_key", forbidden_key)

        forbidden_value = _document("soma_summary")
        payload = forbidden_value["payload"]
        assert isinstance(payload, dict)
        latest = payload["latest_summary_release"]
        assert isinstance(latest, dict)
        components = latest["components"]
        assert isinstance(components, list)
        assert isinstance(components[0], dict)
        components[0]["reference"] = "123456789"
        with self.assertRaises(ValidationError):
            _candidate_from_document("soma_forbidden_value", forbidden_value)

    def test_eia_weekly_identity_uses_scope_and_sorted_content_identity_set(self) -> None:
        base = _candidate("eia_weekly_base")
        scope_changed = _candidate("eia_weekly_scope_change")
        self.assertNotEqual(base.semantic_identity, scope_changed.semantic_identity)

        scope = {"product": "diesel", "geography": "US"}
        rows = [
            {
                "series_id": "macro.eia.weekly.diesel",
                "period": "2026-06-19",
                "content_sha256": "a" * 64,
            },
            {
                "series_id": "macro.eia.weekly.diesel",
                "period": "2026-06-26",
                "content_sha256": "b" * 64,
            },
        ]
        self.assertEqual(
            semantic_identity_eia_weekly(request_scope=scope, rows=rows),
            semantic_identity_eia_weekly(request_scope=scope, rows=list(reversed(rows))),
        )
        with self.assertRaises(ValidationError):
            semantic_identity_eia_weekly(request_scope=scope, rows=[rows[0], rows[0]])

    def test_chicago_fed_and_bis_bind_their_required_material(self) -> None:
        chicago_base = _candidate("chicagofed_base")
        self.assertNotEqual(
            chicago_base.semantic_identity,
            _candidate("chicagofed_scope_change").semantic_identity,
        )
        parsed_changed = _document("chicagofed_base")
        payload = parsed_changed["payload"]
        assert isinstance(payload, dict)
        parsed = payload["parsed_observations"]
        assert isinstance(parsed, list)
        assert isinstance(parsed[0], dict)
        parsed[0]["value"] = "2.1"
        self.assertNotEqual(
            chicago_base.semantic_identity,
            _candidate_from_document("chicago_parsed_changed", parsed_changed).semantic_identity,
        )

        bis_base = _candidate("bis_base")
        bis_scope_changed = copy.deepcopy(_document("bis_base"))
        scope = bis_scope_changed["request_scope"]
        assert isinstance(scope, dict)
        scope["country"] = "CA"
        self.assertNotEqual(
            bis_base.semantic_identity,
            _candidate_from_document("bis_scope_changed", bis_scope_changed).semantic_identity,
        )
        bis_observation_changed = _document("bis_base")
        payload = bis_observation_changed["payload"]
        assert isinstance(payload, dict)
        observations = payload["observations"]
        assert isinstance(observations, list)
        assert isinstance(observations[0], dict)
        observations[0]["value"] = "256.0"
        self.assertNotEqual(
            bis_base.semantic_identity,
            _candidate_from_document("bis_observation_changed", bis_observation_changed).semantic_identity,
        )

    def test_incomplete_capture_states_are_rejected_before_any_writer_can_observe_them(self) -> None:
        base = _document("eia_retail_base")
        for state in ("partial", "error", "timeout", "rate_limit", "http_429", "429"):
            with self.subTest(state=state):
                document = copy.deepcopy(base)
                document["capture_state"] = state
                with self.assertRaises(ValidationError):
                    _candidate_from_document(f"eia_retail_{state}", document)

    def test_fixture_shapes_are_strict_and_fixture_resources_have_no_security_identifiers(self) -> None:
        unexpected = _document("gdp_advance")
        unexpected["unexpected"] = True
        with self.assertRaises(ValidationError):
            _candidate_from_document("gdp_unexpected", unexpected)

        security_value = re.compile(r"\b[0-9A-Z]{9}\b")
        for path in sorted(FIXTURE_ROOT.glob("*.json")):
            with self.subTest(resource=path.name):
                payload = path.read_text(encoding="utf-8")
                self.assertNotIn("CU" + "SIP", payload.upper())
                self.assertIsNone(security_value.search(payload))
