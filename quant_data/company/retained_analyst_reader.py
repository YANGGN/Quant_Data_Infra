"""Fixed capture-membership reads for earnings and consensus research."""
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.json_codec import loads_strict
from quant_data.tool_platform.transcript_access import connection
from quant_data.tool_platform.retained_research_common import instant
from quant_data.company.fmp_analyst_history import ENDPOINTS

MAX_CAPTURES = 128
MAX_ROWS = 4000
MAX_BYTES = 4 * 1024 * 1024

def read_analyst(context, symbol, endpoint, at, *, period=None, history=False, since=None):
    if endpoint not in ENDPOINTS or period not in (None, "annual", "quarter"):
        raise ValidationError("Unsupported retained analyst selection")
    with connection(context) as c:
        clauses = ["symbol=?", "endpoint=?", "captured_at<=?"]
        params = [symbol, endpoint, at]
        if period:
            clauses.append("request_period=?"); params.append(period)
        if history and since:
            # Keep one capture preceding the review window for the first delta.
            prior = c.execute("SELECT captured_at FROM company_fmp_analyst_captures WHERE " +
                " AND ".join(clauses) + " AND captured_at<=? ORDER BY captured_at DESC LIMIT 1",
                (*params, since)).fetchone()
            clauses.append("captured_at>=?"); params.append(prior[0] if prior else since)
        captures = [dict(r) for r in c.execute(
            "SELECT capture_id,issuer_id,cik,symbol,instrument_id,endpoint,request_period,captured_at,"
            "content_sha256,request_scope_json,warnings_json FROM company_fmp_analyst_captures WHERE " +
            " AND ".join(clauses) + " ORDER BY captured_at DESC,capture_id DESC LIMIT ?",
            (*params, MAX_CAPTURES + 1))]
        if history and len(captures) > MAX_CAPTURES:
            raise ResourceLimitError("Analyst history exceeds 128 captures; supply a more recent since cutoff")
        identities = {(r["issuer_id"], r["instrument_id"], r["cik"]) for r in captures}
        if len(identities) > 1:
            raise ValidationError("Retained symbol has ambiguous issuer/security identity")
        if not history: captures = captures[:1]
        rows, size = [], 0
        for cap in reversed(captures):
            context.checkpoint()
            meta = list(c.execute(
                "SELECT v.observation_version_id,length(CAST(v.payload_json AS BLOB)) byte_count "
                "FROM company_fmp_analyst_capture_membership m JOIN company_fmp_analyst_observation_versions v "
                "ON v.observation_version_id=m.observation_version_id WHERE m.capture_id=? LIMIT ?",
                (cap["capture_id"], MAX_ROWS + 1)))
            size += sum(r["byte_count"] for r in meta)
            if len(rows)+len(meta) > MAX_ROWS or size > MAX_BYTES:
                raise ResourceLimitError("Selected analyst inputs exceed the row or byte cap")
            selected = c.execute(
                "SELECT v.*,m.source_row_pointer membership_pointer FROM company_fmp_analyst_capture_membership m "
                "JOIN company_fmp_analyst_observation_versions v ON v.observation_version_id=m.observation_version_id "
                "WHERE m.capture_id=? ORDER BY v.natural_identity", (cap["capture_id"],))
            for item in selected:
                row = dict(item)
                if (row["issuer_id"], row["instrument_id"], row["cik"]) not in identities or instant(row["captured_at"]) > instant(cap["captured_at"]):
                    raise ValidationError("Analyst capture membership identity or timing differs")
                row.update(capture_id=cap["capture_id"], capture_observed_at=instant(cap["captured_at"]),
                    content_sha256=cap["content_sha256"], source_warnings=loads_strict(cap["warnings_json"]),
                    request_scope=loads_strict(cap["request_scope_json"]), payload=loads_strict(row.pop("payload_json")))
                rows.append(row)
    context.budget.require(rows=len(rows), operations=len(rows))
    return rows

def provenance(row):
    return {key: row.get(key) for key in ("symbol", "issuer_id", "instrument_id", "cik",
        "endpoint", "request_period", "capture_id", "capture_observed_at", "observation_version_id",
        "captured_at", "natural_identity", "content_sha256", "source_row_pointer",
        "source_event_date", "source_time_raw", "event_precision", "target_period_end",
        "currency", "estimate_basis", "source_warnings")}
