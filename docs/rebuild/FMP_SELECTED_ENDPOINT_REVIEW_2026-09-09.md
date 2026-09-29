# Selected-universe FMP endpoint review

Scope: private implementation preparation, not provider activation.
The common selection contains 2,248 source symbols. Provider identity, issuer
readiness, actual entitlement and retained coverage determine executable units.

| Family | Storage | Prepared bound per request | Current disposition |
| --- | --- | --- | --- |
| income-statement, balance-sheet-statement, cash-flow-statement, financial-statement-full-as-reported | Existing company FMP research family | Annual and quarter separately; requested limit 1,000; 8 MiB; 30 seconds | Expand supported selected subjects. Stable endpoint maximum/history coverage remains a live preflight question. |
| analyst-estimates | Existing company FMP analyst family | Annual and quarter; 100 rows/page; at most ten pages, 10 MiB and 600 seconds per walk | Expand supported selected subjects. Preserve prospective capture vintages; historical target periods are not historic estimate vintages. |
| dividends, splits | Existing company corporate-action family | 1,000 requested rows; 1 MiB; 30 seconds | Expand through their separate binding and preserve security identity. |
| earnings | Existing company FMP analyst family | 1,000 requested rows; 1 MiB; 30 seconds | Expand separately from estimates and retain the earlier earnings history. |
| grades | Existing company FMP analyst family | 1,000 requested rows; 1 MiB; 30 seconds | Prepared candidate; defer acquisition until entitlement and dated-event coverage are checked. |
| grades-historical | Existing company FMP analyst family | 1,000 requested rows; 1 MiB; 30 seconds | Prepared candidate; preserve distributions independently from individual recommendations. Entitlement/history preflight pending. |
| grades-consensus | Existing company FMP analyst family | One current response; 1 MiB; 30 seconds | Prepared candidate; defer until selected account entitlement is checked. Archive prospectively. |
| price-target | Existing company FMP analyst family | Existing legacy v4 route; 1,000 requested rows; 1 MiB; 30 seconds | Prepared candidate; legacy-route entitlement and chronology must be verified before selecting it. No silent stable/legacy fallback. |
| price-target-consensus, price-target-summary | Existing company FMP analyst family | One current response each; 1 MiB; 30 seconds | Prepared candidates; current summaries are separate from dated targets. Entitlement preflight pending. |
| revenue-product-segmentation | Existing company FMP research family | Annual and quarter separately; requested limit 100; 1 MiB; 30 seconds | Prepared candidate; source categories and all response fields retained. Period/limit behavior and entitlement need preflight. |

All listed limits are local implementation ceilings unless explicitly confirmed
by source documentation. They are not assertions about the account's paid plan.
Other-input acquisition remains an explicit endpoint selection; no automatic
request is generated merely because an endpoint shares a publisher.

Public documentation checked on September 9:
- [FMP product segmentation](https://site.financialmodelingprep.com/developer/docs/stable/revenue-product-segmentation) confirms the stable symbol endpoint and product-level revenue purpose. The retrieved page did not expose parameter maximums.
- [FMP official legacy statement examples](https://github.com/FinancialModelingPrep/income-statement-api) demonstrate annual/quarterly requests with a limit parameter. Legacy examples do not verify a stable-endpoint maximum.
- The stable income-statement and financial-estimates documentation requests
  returned HTTP 403; the grades-historical page was unavailable to the browser.
  No credential was used and no authenticated API request was made.

Activation must bind current prices, statements, estimates, actions, earnings and
FMP news to a coordinated provider allowance. Prepared shared-account coordination now wraps the selected transport and
current market-close, company/macro and news transports. Configuration keeps the
allowance inactive with no invented account ceiling. Source queues retain their
own progress and evidence. Independent verification, measured account limits,
initial already-charged usage and explicit worker cutovers remain prerequisites
to operational activation. Frozen historical population wrappers are not
repurposed or automatically included.
