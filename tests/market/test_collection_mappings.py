from __future__ import annotations
from dataclasses import replace
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from quant_data.market.collection_mappings import (IdentityEvidence,prepare_mapping,unresolved_mapping,CollectionMappingPublisher)
from quant_data.market.collection_bindings import load_bindings,pin_binding,parse_bindings
from quant_data.errors import ConflictError,ValidationError,StoreUnavailableError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.market.collection_universe import CollectionManifestPublisher,parse_manifest
from quant_data.registry import load_registry
from quant_data.migrations import initialize_all
from quant_data.stores import StoreMap,quiet_immutable_read_connection,writer_connection

ROOT=Path(__file__).resolve().parents[2]
AT="2026-09-09T01:00:00Z"
LATER="2026-09-09T02:00:00Z"

class CollectionMappingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir="/tmp")
        self.stores=StoreMap.four_explicit(**{r:Path(self.temp.name)/(r+".sqlite") for r in ("market","macro","company","news")})
        self.registry=load_registry(ROOT/"config/system_registry.json",project_root=ROOT,environment={})
        initialize_all(self.stores,self.registry)
        manifest=parse_manifest(body=b"Symbol,Description\nAAPL,Apple\nBRK.B,Berkshire B\n",universe_id="major_index_liquid",
            name="Major Index Liquid",source_reference="inputs/selection.csv",captured_at=AT)
        receipt=CollectionManifestPublisher(self.stores,self.registry).publish(manifest)
        self.membership=receipt.snapshot_id
        self.run_id=receipt.run_id
        from quant_data.market.stage10_history_importer import _InstrumentPlan
        self.instrument_ids={}
        with writer_connection(self.stores,"market") as c:
            for symbol in ("AAPL","BRK-B","MSFT"):
                plan=_InstrumentPlan(symbol,"equity","Synthetic "+symbol,None)
                self.instrument_ids[symbol]=plan.instrument_id
                c.execute("INSERT INTO stage10_instruments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (plan.instrument_id,"fmp",symbol,"equity",plan.display_name,None,"provider_native",None,plan.identity_seed_sha256,
                     "2026-09-09T01:00:00.000000Z","datetime",self.run_id))
        self.publisher=CollectionMappingPublisher(self.stores,self.registry)
        self.proof=IdentityEvidence(json.dumps([{"symbol":"AAPL","cik":"0000320193"},{"symbol":"BRK-B","cik":"0001067983"}]).encode(),
            "collection/provider-profile.json",AT)
        self.rows=[]
        for i,(symbol,provider_symbol,cik) in enumerate((("AAPL","AAPL","0000320193"),("BRK.B","BRK-B","0001067983"))):
            row=unresolved_mapping(symbol)
            row.update(status="resolved",provider_symbol=provider_symbol,provider_subject=provider_symbol,cik=cik,instrument_id=self.instrument_ids[provider_symbol],
                evidence_sha256=self.proof.sha256,evidence_reference=self.proof.source_reference,evidence_pointer="/"+str(i),
                symbol_field="symbol",subject_field="symbol",cik_field="cik",reason="Explicit source assertion")
            self.rows.append(row)
        self.bindings=load_bindings(ROOT/"config/collection_bindings.json")
    def tearDown(self): self.temp.cleanup()
    def batch(self,rows=None,at=LATER,evidence=None,provider="fmp"):
        # Explicit synthetic reviewer assertions are fixture inputs, not an alias resolver.
        rows=json.loads(json.dumps(self.rows if rows is None else rows))
        proofs=list((self.proof,) if evidence is None else evidence)
        for row in rows:
            if row["status"]=="resolved" and row["source_symbol"]!=row["provider_symbol"] and row["association_sha256"] is None:
                assertion={"contract":"quant_data.reviewed_security_association.v1",
                    "membership_snapshot_id":self.membership,"source_symbol":row["source_symbol"],
                    "provider":provider,"provider_symbol":row["provider_symbol"],"provider_subject":row["provider_subject"],
                    "instrument_id":row["instrument_id"],"provider_evidence_sha256":row["evidence_sha256"],
                    "provider_evidence_pointer":row["evidence_pointer"],"reviewed_by":"synthetic fixture reviewer",
                    "basis":"Synthetic fixture explicitly associates the selected Class B security with this provider Class B record"}
                proof=IdentityEvidence(json.dumps(assertion).encode(),"collection/fixture-alias.json",AT,provider)
                proofs.append(proof);row.update(association_sha256=proof.sha256,association_pointer="")
        return prepare_mapping(membership_snapshot_id=self.membership,provider=provider,captured_at=at,
            source_reference="collection/mappings.json",body=json.dumps(rows).encode(),evidence=tuple(proofs))

    def test_evidenced_aliases_are_separate_from_source_membership(self):
        receipt=self.publisher.publish(self.batch())
        self.assertEqual(receipt.outcome,"succeeded")
        scope=pin_binding(self.stores,self.bindings["fmp_statements"])
        self.assertEqual([r.source_symbol for r in scope.eligible],["AAPL","BRK.B"])
        self.assertEqual([r.provider_symbol for r in scope.eligible],["AAPL","BRK-B"])
        self.assertEqual(scope.ciks,("0000320193","0001067983"))
        with quiet_immutable_read_connection(self.stores,"market") as c:
            self.assertEqual(c.execute("SELECT raw_body FROM market_collection_identity_evidence WHERE evidence_sha256=?",(self.proof.sha256,)).fetchone()[0],self.proof.body)
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(),[])
            self.assertEqual(c.execute("SELECT count(*) FROM stage10_instruments").fetchone()[0],3)

    def test_incomplete_identity_remains_explicit_and_is_not_eligible(self):
        rows=json.loads(json.dumps(self.rows))
        for row in rows: row["instrument_id"]=None
        self.publisher.publish(self.batch(rows))
        binding=self.bindings["dividends_splits"]
        scope=pin_binding(self.stores,binding)
        self.assertEqual(len(scope.eligible),0)
        self.assertEqual({r.status for r in scope.gaps},{"identity_incomplete"})
        self.assertTrue(all("instrument_id" in r.reason for r in scope.gaps))

    def test_missing_and_forged_evidence_are_rejected_before_publication(self):
        before=mutation_fingerprint(self.stores)
        with self.assertRaises(ValidationError): self.batch(evidence=())
        rows=json.loads(json.dumps(self.rows));rows[1]["provider_symbol"]="BRK-A"
        with self.assertRaises(ValidationError): self.batch(rows)
        rows=json.loads(json.dumps(self.rows));rows[1]["cik"]="0000320193"
        with self.assertRaises(ValidationError): self.batch(rows)
        with self.assertRaises(ValidationError): self.batch(evidence=(replace(self.proof,captured_at="2026-09-10T00:00:00Z"),))
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_mapping_must_account_for_every_selected_member(self):
        before=mutation_fingerprint(self.stores)
        with self.assertRaises(ValidationError): self.publisher.publish(self.batch(self.rows[:1]))
        self.assertEqual(before,mutation_fingerprint(self.stores))
        pending=[unresolved_mapping("AAPL"),unresolved_mapping("BRK.B")]
        self.publisher.publish(self.batch(pending,evidence=()))
        self.assertEqual(len(pin_binding(self.stores,self.bindings["fmp_statements"]).gaps),2)

    def test_mapping_replay_and_reordered_raw_evidence_cause_no_writes(self):
        self.publisher.publish(self.batch())
        before=mutation_fingerprint(self.stores)
        self.assertEqual(self.publisher.publish(self.batch()).outcome,"unchanged")
        self.assertEqual(self.publisher.publish(self.batch(list(reversed(self.rows)),at="2026-09-09T03:00:00Z")).outcome,"unchanged")
        other=replace(self.proof,body=json.dumps(json.loads(self.proof.body),indent=2).encode(),captured_at=LATER)
        rows=json.loads(json.dumps(self.rows))
        for row in rows: row["evidence_sha256"]=other.sha256
        self.assertEqual(self.publisher.publish(self.batch(rows,evidence=(other,),at="2026-09-09T03:00:00Z")).outcome,"unchanged")
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_mapping_changes_pin_old_state_and_respect_cutoff(self):
        pending=[unresolved_mapping("AAPL"),unresolved_mapping("BRK.B")]
        initial=self.batch(pending,evidence=(),at=LATER)
        self.publisher.publish(initial)
        old=pin_binding(self.stores,self.bindings["fmp_statements"])
        self.publisher.publish(self.batch(at="2026-09-09T03:00:00Z"))
        current=pin_binding(self.stores,self.bindings["fmp_statements"])
        self.assertNotEqual(old.mapping_id,current.mapping_id)
        self.assertEqual(len(old.gaps),2)
        self.assertEqual(len(current.eligible),2)
        self.assertEqual(pin_binding(self.stores,self.bindings["fmp_statements"],cutoff=LATER),old)
        before=mutation_fingerprint(self.stores)
        self.assertEqual(self.publisher.publish(initial).outcome,"unchanged")
        self.assertEqual(before,mutation_fingerprint(self.stores))
        with self.assertRaises(StoreUnavailableError):
            pin_binding(self.stores,self.bindings["fmp_statements"],cutoff=AT)

    def test_mapping_tables_are_immutable_and_replacement_is_forbidden(self):
        self.publisher.publish(self.batch())
        with writer_connection(self.stores,"market") as c:
            for table in ("market_collection_mapping_snapshots","market_collection_provider_mappings",
                          "market_collection_identity_evidence","market_collection_mapping_heads"):
                with self.subTest(table=table),self.assertRaises(sqlite3.IntegrityError):
                    c.execute("DELETE FROM "+table)
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT OR REPLACE INTO market_collection_provider_mappings SELECT * FROM market_collection_provider_mappings LIMIT 1")
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("UPDATE market_collection_mapping_heads SET mapping_id=mapping_id")

    def test_binding_matrix_keeps_options_and_macro_independent(self):
        self.assertEqual(len(self.bindings),10)
        self.assertNotIn("options",self.bindings)
        self.assertNotIn("macro",self.bindings)
        self.assertEqual(self.bindings["daily_prices"].retain_universes,("curated_etfs","major_indexes"))
        raw=json.loads((ROOT/"config/collection_bindings.json").read_bytes())
        for binding in raw["bindings"]:
            binding["mode"]="prepared"
        prepared=parse_bindings(json.dumps(raw).encode())
        self.assertTrue(all(b.mode=="prepared" for b in prepared.values()))
        with self.assertRaises(ValidationError):
            pin_binding(self.stores,replace(self.bindings["fmp_statements"],provider="sharadar"))

    def test_sec_subject_requests_deduplicate_cik_without_dropping_members(self):
        proof=replace(self.proof,provider="sec",body=json.dumps([{"symbol":"AAPL","cik":"0000320193"},{"symbol":"BRK-B","cik":"0000320193"}]).encode())
        rows=json.loads(json.dumps(self.rows))
        for row in rows:
            row.update(cik="0000320193",provider_subject="0000320193",subject_field="cik",evidence_sha256=proof.sha256,instrument_id=None)
        self.publisher.publish(self.batch(rows,evidence=(proof,),provider="sec"))
        scope=pin_binding(self.stores,self.bindings["sec_filings_companyfacts"])
        self.assertEqual(len(scope.eligible),2)
        self.assertEqual(scope.ciks,("0000320193",))

    def test_mapping_return_to_prior_state_creates_a_new_transition(self):
        initial=self.batch()
        self.publisher.publish(initial)
        first=pin_binding(self.stores,self.bindings["fmp_statements"])
        self.publisher.publish(self.batch([unresolved_mapping("AAPL"),unresolved_mapping("BRK.B")],
            evidence=(),at="2026-09-09T03:00:00Z"))
        self.publisher.publish(self.batch(at="2026-09-09T04:00:00Z"))
        current=pin_binding(self.stores,self.bindings["fmp_statements"])
        self.assertEqual(first.subjects,current.subjects)
        self.assertNotEqual(first.mapping_id,current.mapping_id)
        with quiet_immutable_read_connection(self.stores,"market") as c:
            self.assertEqual(c.execute("SELECT count(*) FROM market_collection_mapping_snapshots").fetchone()[0],3)

    def test_provider_bundle_swap_without_reviewed_source_association_is_rejected(self):
        rows=json.loads(json.dumps(self.rows))
        rows[0].update({k:rows[1][k] for k in rows[1] if k!="source_symbol"})
        with self.assertRaises(ValidationError):
            prepare_mapping(membership_snapshot_id=self.membership,provider="fmp",captured_at=LATER,
                source_reference="collection/swap.json",body=json.dumps(rows).encode(),evidence=(self.proof,))

    def test_non_fmp_instrument_link_requires_time_valid_same_member_fmp_mapping(self):
        eqproof=replace(self.proof,provider="equibles",source_reference="collection/equibles.json")
        initial=json.loads(json.dumps(self.rows))
        for row in initial: row["evidence_reference"]=eqproof.source_reference
        with self.assertRaises(ConflictError):
            self.publisher.publish(self.batch(initial,evidence=(eqproof,),provider="equibles"))
        self.publisher.publish(self.batch())
        # Independently captured provider responses retain byte-identical originals.
        self.assertEqual(eqproof.body,self.proof.body)
        rows=json.loads(json.dumps(self.rows))
        for row in rows: row["evidence_sha256"]=eqproof.sha256;row["evidence_reference"]=eqproof.source_reference
        self.assertEqual(self.publisher.publish(self.batch(rows,evidence=(eqproof,),provider="equibles",at="2026-09-09T03:00:00Z")).outcome,"succeeded")
        with quiet_immutable_read_connection(self.stores,"market") as c:
            captures=c.execute("SELECT provider,raw_body FROM market_collection_identity_evidence WHERE evidence_sha256=? ORDER BY provider",(self.proof.sha256,)).fetchall()
            self.assertEqual([r[0] for r in captures],["equibles","fmp"])
            self.assertTrue(all(r[1]==self.proof.body for r in captures))
        wrong=json.loads(json.dumps(rows));wrong[0]["instrument_id"]=self.instrument_ids["MSFT"]
        with self.assertRaises(ConflictError):
            self.publisher.publish(self.batch(wrong,evidence=(eqproof,),provider="equibles",at="2026-09-09T04:00:00Z"))

    def test_retained_evidence_cannot_be_relabelled_to_an_earlier_capture(self):
        later=replace(self.proof,captured_at="2026-09-09T09:00:00Z")
        self.publisher.publish(self.batch(evidence=(later,),at="2026-09-09T10:00:00Z"))
        later_membership=parse_manifest(body=b"Symbol,Description\nAAPL,Apple updated\nBRK.B,Berkshire B\n",
            universe_id="major_index_liquid",name="Major Index Liquid",source_reference="inputs/next.csv",
            captured_at="2026-09-09T01:30:00Z")
        self.membership=CollectionManifestPublisher(self.stores,self.registry).publish(later_membership).snapshot_id
        before=mutation_fingerprint(self.stores)
        earlier=replace(self.proof,captured_at="2026-09-09T04:00:00Z",source_reference="collection/relabelled.json")
        rows=json.loads(json.dumps(self.rows))
        for row in rows: row["evidence_reference"]=earlier.source_reference
        with self.assertRaises(ConflictError):
            self.publisher.publish(self.batch(rows,evidence=(earlier,),at="2026-09-09T05:00:00Z"))
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_normalized_ciks_require_exactly_ten_ascii_digits(self):
        for cik in ("00000320193","٠٠٠٠٣٢٠١٩٣"):
            proof=replace(self.proof,body=json.dumps([{"symbol":"AAPL","cik":cik},{"symbol":"BRK-B","cik":"0001067983"}]).encode())
            rows=json.loads(json.dumps(self.rows));rows[0]["cik"]=cik
            for row in rows: row["evidence_sha256"]=proof.sha256
            with self.subTest(cik=cik),self.assertRaises(ValidationError):
                self.batch(rows,evidence=(proof,))

    def test_identity_evidence_cannot_change_provider(self):
        with self.assertRaises(ValidationError):
            self.batch(provider="equibles")

    def test_retained_etf_index_snapshots_are_pinned_and_time_filtered(self):
        from quant_data.market.stage10_history_importer import _InstrumentPlan
        self.publisher.publish(self.batch())
        with writer_connection(self.stores,"market") as c:
            c.execute("INSERT INTO stage10_scope_snapshots VALUES (?,?,?,?,?,?,?)",
                ("fixture_scope","0"*64,"stage10_fmp_market_history_v1","fmp","2026-09-09T01:00:00.000000Z","datetime",self.run_id))
            for universe in ("curated_etfs","major_indexes"):
                c.execute("INSERT INTO stage10_universes VALUES (?,?,?,?,?)",
                    (universe,"instrument_watchlist","Synthetic fixture","reviewed_local_manifest",self.run_id))
            plans={}
            for symbol,kind in (("SPY","etf"),("QQQ","etf"),("^GSPC","index")):
                plan=_InstrumentPlan(symbol,kind,"Synthetic "+symbol,None);plans[symbol]=plan
                c.execute("INSERT INTO stage10_instruments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (plan.instrument_id,"fmp",symbol,kind,plan.display_name,None,"provider_native",None,plan.identity_seed_sha256,
                     "2026-09-09T01:00:00.000000Z","datetime",self.run_id))
            for snapshot,universe,symbols,at in (("etfs_v1","curated_etfs",("SPY",),"2026-09-09T01:00:00.000000Z"),
                ("indexes_v1","major_indexes",("^GSPC",),"2026-09-09T01:00:00.000000Z")):
                sha=hashlib.sha256(json.dumps(sorted(symbols)).encode()).hexdigest()
                c.execute("INSERT INTO stage10_universe_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (snapshot,universe,"fixture_scope",None,"2026-09-09",at,"datetime","complete",
                     "reviewed_instrument_watchlist",len(symbols),sha,self.run_id))
                for number,symbol in enumerate(symbols,1):
                    c.execute("INSERT INTO stage10_universe_snapshot_members VALUES (?,?,?)",(snapshot,plans[symbol].instrument_id,number))
        old=pin_binding(self.stores,self.bindings["daily_prices"])
        self.assertEqual({r.provider_symbol for r in old.retained},{"SPY","^GSPC"})
        self.assertEqual({s.source_symbol for s in old.eligible},{"AAPL","BRK.B"})
        with writer_connection(self.stores,"market") as c:
            c.execute("INSERT INTO stage10_universe_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("etfs_v2","curated_etfs","fixture_scope",None,"2026-09-09","2026-09-09T03:00:00.000000Z",
                 "datetime","complete","reviewed_instrument_watchlist",2,"1"*64,self.run_id))
            for number,symbol in enumerate(("SPY","QQQ"),1):
                c.execute("INSERT INTO stage10_universe_snapshot_members VALUES (?,?,?)",("etfs_v2",plans[symbol].instrument_id,number))
        current=pin_binding(self.stores,self.bindings["daily_prices"])
        self.assertEqual({r.provider_symbol for r in current.retained},{"SPY","QQQ","^GSPC"})
        self.assertNotEqual(old.scope_sha256,current.scope_sha256)
        self.assertEqual(pin_binding(self.stores,self.bindings["daily_prices"],cutoff=LATER),old)
        self.assertEqual({r.provider_symbol for r in old.retained},{"SPY","^GSPC"})
        self.assertEqual(pin_binding(self.stores,self.bindings["news"]).retained,current.retained)

    def test_identical_bytes_recaptured_later_replay_without_writes(self):
        self.publisher.publish(self.batch())
        before=mutation_fingerprint(self.stores)
        later=replace(self.proof,captured_at="2026-09-09T03:00:00Z")
        self.assertEqual(self.publisher.publish(self.batch(evidence=(later,),at="2026-09-09T04:00:00Z")).outcome,"unchanged")
        self.assertEqual(before,mutation_fingerprint(self.stores))

    def test_changed_mapping_can_use_new_acquisition_of_identical_bytes(self):
        self.publisher.publish(self.batch())
        rows=json.loads(json.dumps(self.rows))
        rows[0]["reason"]="A newly reviewed identity assertion"
        later=replace(self.proof,captured_at="2026-09-09T03:00:00Z")
        self.assertEqual(self.publisher.publish(self.batch(rows,evidence=(later,),at="2026-09-09T04:00:00Z")).outcome,"succeeded")
        with quiet_immutable_read_connection(self.stores,"market") as c:
            captures=c.execute("SELECT evidence_id,captured_at,raw_body FROM market_collection_identity_evidence WHERE provider='fmp' AND evidence_sha256=? ORDER BY captured_at",(self.proof.sha256,)).fetchall()
            self.assertEqual(len(captures),2)
            self.assertNotEqual(captures[0][0],captures[1][0])
            self.assertTrue(all(r[2]==self.proof.body for r in captures))
