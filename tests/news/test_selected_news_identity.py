from dataclasses import replace
import json
import unittest

from quant_data.errors import ValidationError, ResourceLimitError
from quant_data.market.collection_mappings import IdentityEvidence
from quant_data.news.selected_identity import prepare_alpaca_news_mapping


class SelectedNewsIdentityTests(unittest.TestCase):
    def prepare(self, assets, instruments=None, **changes):
        args = dict(membership_snapshot_id="selection", instruments=instruments or {"NEW": "fmp_new"},
                    evidence=IdentityEvidence(json.dumps(assets).encode(), "fixture/assets.json",
                                              "2026-09-11T00:00:00Z", "alpaca"),
                    captured_at="2026-09-11T01:00:00Z", source_reference="fixture/news-map.json")
        args.update(changes)
        return prepare_alpaca_news_mapping(**args)

    def asset(self, symbol="NEW", **changes):
        return {"symbol": symbol, "id": "asset-"+symbol, "class": "us_equity", "status": "active", **changes}

    def test_exact_security_evidence_and_raw_body_are_preserved(self):
        batch = self.prepare([self.asset(tradable=False)])
        row = json.loads(batch.rows_json)[0]
        self.assertEqual((row["provider_symbol"], row["provider_subject"], row["instrument_id"]),
                         ("NEW", "asset-NEW", "fmp_new"))
        self.assertEqual(row["evidence_pointer"], "/0")
        self.assertEqual(batch.evidence[0].body, json.dumps([self.asset(tradable=False)]).encode())

    def test_unmapped_inactive_and_conflicting_assets_remain_explicit(self):
        assets = [self.asset("OLD", status="inactive"), self.asset("DUP"), self.asset("DUP", id="other")]
        batch = self.prepare(assets, {"OLD":"old", "DUP":"dup", "MISS":"missing"})
        rows = {r["source_symbol"]:r for r in json.loads(batch.rows_json)}
        self.assertEqual({s:r["status"] for s,r in rows.items()},
                         {"OLD":"unsupported", "DUP":"ambiguous", "MISS":"unresolved"})
        self.assertTrue(all(r["instrument_id"] is None for r in rows.values()))
        self.assertEqual(batch.evidence, ())

    def test_dot_dash_share_classes_are_never_guessed(self):
        batch = self.prepare([self.asset("BRK-B")], {"BRK.B":"brk_b"})
        self.assertEqual(json.loads(batch.rows_json)[0]["status"], "unresolved")

    def test_full_selected_roster_and_etfs_fit_existing_mapping_contract(self):
        instruments = {f"S{i:04}":f"instrument{i}" for i in range(2344)}
        assets = [self.asset(s) for s in reversed(tuple(instruments))]
        batch = self.prepare(assets, instruments)
        rows = json.loads(batch.rows_json)
        self.assertEqual(len(rows), 2344)
        self.assertEqual([r["source_symbol"] for r in rows], sorted(instruments))
        self.assertTrue(all(r["status"] == "resolved" for r in rows))

    def test_future_wrong_provider_and_malformed_evidence_fail(self):
        proof = IdentityEvidence(b"[]", "fixture/assets.json", "2026-09-12T00:00:00Z", "alpaca")
        with self.assertRaises(ValidationError):self.prepare([], evidence=proof)
        with self.assertRaises(ValidationError):self.prepare([], evidence=replace(proof, provider="fmp"))
        with self.assertRaises(ValidationError):self.prepare([self.asset(id=None)])
        with self.assertRaises(ValidationError):self.prepare([self.asset(**{"class":"crypto"})])
        with self.assertRaises(ResourceLimitError):self.prepare({"assets":[]})
