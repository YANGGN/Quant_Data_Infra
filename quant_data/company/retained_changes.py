"""Bounded company capture and extraction metadata changes."""
from quant_data.errors import ResourceLimitError
from quant_data.tool_platform.transcript_access import connection

def changes(context,symbol,since,at,domains):
    rows=[]
    with connection(context) as c:
        if "earnings" in domains or "estimates" in domains:
            endpoints=tuple(e for domain,e in (("earnings","earnings"),("estimates","analyst-estimates")) if domain in domains)
            source=c.execute("SELECT observation_version_id,source_capture_id,natural_identity,instrument_id,"
                "endpoint,request_period,target_period_end,source_event_date,captured_at,version_sequence "
                "FROM company_fmp_analyst_observation_versions WHERE symbol=? AND captured_at>? AND captured_at<=? "
                "AND endpoint IN ("+",".join("?" for _ in endpoints)+") ORDER BY captured_at,observation_version_id LIMIT 1001",
                (symbol,since,at,*endpoints)).fetchall()
            if len(source)>1000: raise ResourceLimitError("Company changes exceed 1000 rows; shorten the review window")
            rows += [(r["endpoint"],dict(r)) for r in source]
        if "transcripts" in domains:
            source=c.execute("SELECT capture_id,instrument_id,event_id,fiscal_year,fiscal_quarter,captured_at "
                "FROM company_equibles_transcripts WHERE symbol=? AND captured_at>? AND captured_at<=? "
                "ORDER BY captured_at,capture_id LIMIT 1001",(symbol,since,at)).fetchall()
            if len(source)>1000: raise ResourceLimitError("Transcript changes exceed 1000 rows")
            rows += [("transcript_capture",dict(r)) for r in source]
            drafts=c.execute("SELECT o.analysis_id,o.capture_id,o.available_at,o.schema_version,o.model,"
                "t.instrument_id,t.fiscal_year,t.fiscal_quarter FROM company_structured_transcript_outputs o "
                "JOIN company_equibles_transcripts t ON t.capture_id=o.capture_id "
                "WHERE t.symbol=? AND o.available_at>? AND o.available_at<=? AND o.source_available_at<=? AND t.captured_at<=? "
                "ORDER BY o.available_at,o.analysis_id LIMIT 1001",(symbol,since,at,at,at)).fetchall()
            if len(drafts)>1000: raise ResourceLimitError("Transcript draft changes exceed 1000 rows")
            rows += [("transcript_draft",dict(r)) for r in drafts]
    return rows
