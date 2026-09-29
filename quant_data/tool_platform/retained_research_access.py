"""Closed resolver for the retained-data research additions."""
from collections.abc import Mapping
from datetime import datetime,timedelta
from quant_data.errors import ValidationError
from .retained_research_contracts import TOOLS,KINDS,VERSIONS,RetainedResearchArguments,parse
from .retained_research_common import cutoff

def plain(value):
    if isinstance(value,Mapping):return {k:plain(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [plain(v) for v in value]
    return value

def invoke(name,arguments,context,registry):
    if not isinstance(arguments,RetainedResearchArguments) or arguments.kind!=KINDS[name]:
        raise ValidationError("Retained research requires its registered typed arguments")
    args=plain(parse(arguments.kind,plain(arguments)))
    if name!="data.get_collection_plan":
        at=cutoff(args,context)
        args["as_of"]=at
        if name=="research.get_watchlist_changes":
            if (datetime.fromisoformat(at.replace("Z","+00:00"))-datetime.fromisoformat(args["since"].replace("Z","+00:00")))>timedelta(days=31):
                raise ValidationError("Watchlist review windows are limited to 31 days")
    from . import earnings_research,consensus_research,volatility_comparison,transcript_comparison
    from . import fundamental_screen,watchlist_changes,event_distribution,market_breadth,research_coverage,collection_plan
    operation={
        TOOLS[0]:earnings_research.calendar,TOOLS[1]:earnings_research.setup,
        TOOLS[2]:consensus_research.history,TOOLS[3]:consensus_research.revisions,
        TOOLS[4]:volatility_comparison.invoke,TOOLS[5]:transcript_comparison.invoke,
        TOOLS[6]:fundamental_screen.invoke,TOOLS[7]:watchlist_changes.invoke,
        TOOLS[8]:event_distribution.invoke,TOOLS[9]:market_breadth.invoke,
        TOOLS[10]:research_coverage.invoke,TOOLS[11]:collection_plan.invoke}[name]
    context.checkpoint()
    value=operation(args,context,registry)
    context.checkpoint()
    context.budget.require(rows=len(value.records))
    return value
