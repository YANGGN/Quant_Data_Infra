"""Versioned evidence summaries and a compact cross-sector guidance checklist."""

VERSION = "transcript_analysis_prompt.v3"
CHECKLIST_VERSION = "transcript_guidance_checklist.v1"

GUIDANCE_CHECKLIST = """Guidance coverage checklist (apply only where relevant to the company):
1. Revenue and demand: revenue/sales, volumes, pricing/mix, orders, bookings/backlog, customer growth/retention, subscriptions/ARR, traffic and comparable sales.
2. Earnings and returns: EPS/net income, EBIT/EBITDA, gross/operating margins, segment profit, returns and the company's own adjusted or sector measures.
3. Costs and productivity: operating expenses, labor/input costs, R&D, depreciation, restructuring, efficiency and cost-saving targets; distinguish total from incremental savings.
4. Cash and capital allocation: operating/free cash flow, working capital/inventory, capex, dividends, buybacks, debt, leverage, liquidity and financing.
5. Operating capacity and execution: production/deliveries, utilization, supply constraints, capacity additions, project ramp-ups, launches and milestone timing.
6. Sector drivers when discussed: banks/insurers (loans/deposits, net interest income/margin, credit losses, capital, premiums/combined ratio); real estate (FFO/AFFO, NOI, occupancy/rents); resources/utilities (output, realized prices, unit costs, reserves, rate base); technology/telecom (usage, ARR, churn, subscribers); healthcare (enrollment, medical costs, trials/approvals); consumer/industrial/transport (comps, units, backlog, load factors). Retain other company-defined KPIs.
7. Assumptions and sensitivities: FX, commodities, rates, tax, regulation/incentives, geography/customer demand, mix, weather and execution; attach stated dependencies and upside/downside scenarios to the affected claims.
8. Changes and limits: new/reiterated/raised/lowered/withdrawn guidance, prior ranges, explicit refusal to quantify, provisional estimates and possible deviations from a stated range.
For every item, retain metric and scope, horizon/comparison, accounting/growth basis, value/unit/scale and uncertainty. Cover numeric and qualitative outlooks from prepared remarks AND Q&A; unknown or unmentioned is not zero and silence is not refusal.
Reconcile repeated mentions across the call: merge timing, assumptions and caveats into the same claim, while keeping distinct metrics/scopes/horizons separate. Reconcile forward-looking analyst-response summaries with guidance records. Do not lose a Q&A refinement during deduplication.
Exclude historical actuals, analyst hypotheses, generic aspirations and market-size estimates that are not company guidance. Preserve explicit operating plans/targets and refusals. Briefly explain material exclusions or unresolved coverage in coverage_notes; do not emit empty sector templates.
"""

EVIDENCE_RULES = """
Evidence entries contain evidence_summary and an existing turn_id. Summaries may paraphrase source wording and must preserve its meaning, numbers, qualifications and uncertainty. They are evidence summaries, not verbatim quotations: do not wrap them in quotation marks or invent text offsets. Use management turns for guidance/tone and the assigned analyst/management turns for question/answer evidence. Source IDs, speaker roles and exchange membership remain mandatory.
"""

REVIEW_RULES = """
Review substantive omissions, altered meaning, unsupported conclusions and incorrect numbers; differences in wording, capitalization or punctuation are not findings by themselves. For an omission, identify the source turn, missing information, any existing claim that only partly covers it, and whether to add a claim or enrich a qualification. Distinguish permitted exclusions and source ambiguities from errors. Tone requires separate overall, prepared-remarks, Q&A and speaker assessments; a joint speaker-by-section grid is not required. Proposed corrections remain descriptions, not automatically accepted data.
"""
