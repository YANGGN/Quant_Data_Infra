# Cross-sector guidance checklist

Version: transcript_guidance_checklist.v1. Included in Terra extraction and Sol review
for prompt v3 and v4 requests. Apply relevant items only; this is a coverage aid,
not a requirement to manufacture guidance for every category.

Guidance coverage checklist (apply only where relevant to the company):
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

The current strict [analysis schema](TRANSCRIPT_ANALYSIS_V3.schema.json) makes
this checklist reviewable through guidance_coverage: all management turn IDs,
candidate summaries/source IDs, included/merged claim links or explicit exclusion
reasons. Q&A topics reference candidate IDs. Repeated mentions retain later
timing, assumptions and caveats in the linked claim and its evidence.
Prompt v3 retains its original schema v2 without this ledger.

Qualitative guidance retains null numeric fields; explicit refusal/withdrawal
uses shape none. Numeric strings use the source magnitude plus scale. The
[example](TRANSCRIPT_ANALYSIS_V3.example.json) shows how prepared guidance and
two Q&A refinements share one numeric claim. Sol's
[review schema](TRANSCRIPT_REVIEW_V3.schema.json) separates missing claims,
missing qualifications, interpretation errors and acceptable exclusions.

Model requests retain Terra/high extraction, Sol/high pilot review and the
20-minute Codex deadline. No extra model pass is introduced by this checklist.
