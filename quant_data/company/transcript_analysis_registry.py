"""Private, company-owned derived analysis of retained transcript evidence."""
import copy
import hashlib
from pathlib import Path
from .equibles_registry import COLLECTOR as BASE_COLLECTOR

DATASET_IDS = ("company.transcript.analysis",)
MIGRATION_ID = "company:0011_transcript_analysis"
MIGRATION_RESOURCE = "quant_data/migrations/company/0011_transcript_analysis.sql"
TABLES = ("company_transcript_analyses", "company_guidance_claims", "company_analyst_question_blocks",
          "company_analyst_topics", "company_management_tone_assessments", "company_transcript_analysis_reviews")
COLLECTOR = copy.deepcopy(BASE_COLLECTOR)
COLLECTOR.update(id="openai.company.transcript_analysis", handler="company.transcript_analysis",
    configuration_env=["OPENAI_API_KEY"], input_datasets=["company.equibles.transcripts"],
    output_datasets=list(DATASET_IDS),
    semantic_identity={"includes": ["request_scope", "input_sha256", "model", "reasoning_effort", "prompt_sha256", "schema_sha256", "parser_version", "validated_output"],
                       "excludes": ["api_key", "transport_headers", "token_usage"]},
    workload_bounds={"max_requests": 20, "max_rows": 10000, "max_bytes": 4194304, "max_seconds": 3600})


def add_declarations(raw, project_root):
    if any(d["id"] in DATASET_IDS for d in raw["datasets"]):
        raise ValueError("Transcript analysis declarations already exist")
    dataset = copy.deepcopy(next(d for d in raw["datasets"] if d["id"] == "company.equibles.transcripts"))
    dataset.update(id=DATASET_IDS[0], layer="derived", collector_ids=[COLLECTOR["id"]],
        physical={"relations": [{"kind": "table", "name": n} for n in TABLES]},
        identity={"stable_fields": ["capture_id", "request_identity"], "version_fields": ["analysis_id", "review_id"]})
    dataset["temporal"]["availability_fields"] = ["available_at"]
    dataset["temporal"]["observation_fields"] = ["available_at"]
    dataset["quality_contract"]["rules"] = ["derived_model_interpretation", "exact_source_spans", "local_analysis_availability", "strict_schema_and_semantics", "computed_question_frequency", "review_is_not_human_verification"]
    raw["datasets"].append(dataset)
    raw["collectors"].append(copy.deepcopy(COLLECTOR))
    resource = project_root / MIGRATION_RESOURCE
    if not resource.exists():
        resource = Path(__file__).resolve().parents[2] / MIGRATION_RESOURCE
    migration = {"id": MIGRATION_ID, "store": "company", "ordinal": 11,
        "dependencies": ["company:0010_equibles_transcripts"], "resource": MIGRATION_RESOURCE,
        "sha256": hashlib.sha256(resource.read_bytes()).hexdigest(), "reconstruction_state": "fixture_validated",
        "semantic_scope": "Append derived Terra transcript guidance, analyst focus and management wording assessments with exact source lineage, immutable Sol reviews and local availability; preserve original transcripts and existing guidance facts."}
    raw["migrations"].insert(next(i for i,m in enumerate(raw["migrations"]) if m["store"]=="news"), migration)
    next(s for s in raw["stores"] if s["id"]=="company")["migration_order"].append(MIGRATION_ID)
    raw["registry_version"] = "2.75.0"
