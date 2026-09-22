"""Private host command for preparing and importing the selected collection CSV."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
from pathlib import Path
from ..errors import ValidationError
from ..json_codec import dumps_strict
from ..market.collection_bindings import load_bindings
from ..market.collection_registry import MIGRATION_ID,DATASET_IDS
from ..market.collection_universe import parse_manifest,CollectionManifestPublisher,MAX_BYTES
from ..migrations import migrate_and_register_store
from ..registry import load_registry
from ..stores import StoreMap,quiet_immutable_read_connection

PROJECT_ROOT=Path("/home/volatility/Python_Projects/Quant_Data_Infra")
SOURCE_NAME="Major Index Liquid_2026-09-08.csv"
SOURCE_SHA256="9393c72160eae41fc3af10a16af4ecc5ab2ab8f8bb7a5d4130162fa9e4aff15a"

def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00","Z")

def prepare_selected(project_root,*,captured_at):
    import hashlib
    bindings=load_bindings(Path(project_root)/"config/collection_bindings.json")
    with (Path(project_root)/SOURCE_NAME).open("rb") as handle: body=handle.read(MAX_BYTES+1)
    if hashlib.sha256(body).hexdigest()!=SOURCE_SHA256:
        raise ValidationError("Selected CSV does not match the user-approved source hash")
    parsed=parse_manifest(body=body,universe_id="major_index_liquid",name="Major Index Liquid",
        source_reference=SOURCE_NAME,source_label_date="2026-09-08",captured_at=captured_at)
    if len(parsed.members)!=2248: raise ValidationError("Selected CSV member count differs")
    report={"contract":"quant_data.collection_preflight","version":"1.0.0","source_path":SOURCE_NAME,
        "source_sha256":parsed.content_sha256,"membership_sha256":parsed.membership_sha256,
        "universe_id":parsed.universe_id,"selected_members":len(parsed.members),
        "captured_at":parsed.captured_at,"provider_requests":0,"canonical_writes":0,
        "bindings":[{"id":b.id,"provider":b.provider,"mode":b.mode,"retained_universes":list(b.retain_universes)}
            for b in bindings.values()],
        "provider_mapping_status":"not_checked","filename_date_is_historical_availability":False}
    return parsed,report

def _expected(registry):
    return [(m.id,m.store,m.ordinal,m.resource,m.sha256,m.reconstruction_state) for m in sorted(registry.migrations,key=lambda m:m.ordinal) if m.store=="market"]

def _ledger(c):
    return [tuple(r) for r in c.execute("SELECT migration_id,store_role,ordinal,resource,sha256,reconstruction_state FROM schema_migrations ORDER BY ordinal")]

def ready(stores,registry):
    with quiet_immutable_read_connection(stores,"market") as c:
        if _ledger(c)!=_expected(registry): raise ValidationError("Collection migration ledger is not ready")
        for declaration in (d for d in registry.datasets_for("market") if d.id in DATASET_IDS):
            row=c.execute("SELECT store_role,layer,schema_version,relations_json,active FROM dataset_registry WHERE dataset_id=?",(declaration.id,)).fetchone()
            identity=c.execute("SELECT identity_sha256 FROM dataset_identity_contracts WHERE dataset_id=?",(declaration.id,)).fetchone()
            if (row is None or tuple(row)!=(declaration.store,declaration.layer,declaration.schema_version,dumps_strict(list(declaration.relations)),int(declaration.active))
                or identity is None or identity[0]!=declaration.identity_sha256):
                raise ValidationError("Collection dataset registration differs")
    return True

def apply_schema(stores,registry,*,applied_at):
    expected=_expected(registry)
    if not expected or expected[-1][0]!=MIGRATION_ID:
        raise ValidationError("Collection migration is not the expected current market head")
    with quiet_immutable_read_connection(stores,"market") as c:
        actual=_ledger(c)
    if actual not in (expected,expected[:-1]):
        raise ValidationError("Collection predecessor migration ledger differs")
    migrate_and_register_store(stores,registry,"market",applied_at=applied_at)
    ready(stores,registry)
    return {"outcome":"ready","migration_id":MIGRATION_ID,"provider_requests":0}

def import_selected(stores,registry,manifest):
    ready(stores,registry)
    return CollectionManifestPublisher(stores,registry).publish(manifest)

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=("prepare","apply-schema","import-selected"))
    args=parser.parse_args(argv)
    # Preparing the exact source is store-free, credential-free and network-free.
    manifest,report=prepare_selected(PROJECT_ROOT,captured_at=now())
    if args.command=="prepare":
        print(dumps_strict(report));return 0
    registry=load_registry(PROJECT_ROOT/"config/system_registry.json",project_root=PROJECT_ROOT,environment={})
    stores=StoreMap.four_explicit(**{r:PROJECT_ROOT/"data"/(r+".sqlite") for r in ("market","macro","company","news")})
    if args.command=="apply-schema": result=apply_schema(stores,registry,applied_at=now())
    else:
        receipt=import_selected(stores,registry,manifest)
        result={"outcome":receipt.outcome,"universe_id":manifest.universe_id,"selected_members":len(manifest.members),
            "snapshot_id":receipt.snapshot_id,"written_count":receipt.written_count,"provider_requests":0}
    print(dumps_strict(result));return 0

if __name__=="__main__": raise SystemExit(main())
