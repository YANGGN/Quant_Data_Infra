"""Fixed collector metadata for status views and private run receipts.

Declarations describe existing wrappers; they neither prove activation nor grant
execution authority. Archived receipt decoders stay separate from timer views.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class TimerBinding:
    unit: str
    label: str
    datasets: tuple[str, ...]


@dataclass(frozen=True)
class CollectorDefinition:
    unit: str
    label: str
    datasets: tuple[str, ...]
    arguments: tuple[str, ...]
    description: str | None


# Preserve the existing timer-view order and exact labels/bindings.
COLLECTORS = (
    CollectorDefinition('quant-data-market-close.timer', 'Market close', (
        'market.stage10.daily_prices',
        'market.stage10.source_evidence',
    ), (), 'Daily equity, ETF and index prices'),
    CollectorDefinition('quant-data-theta-options-daily.timer', 'Theta options daily', (
        'options.theta.receipts',
        'options.theta.selected_contracts',
        'options.theta.daily_research',
    ), ('--mode', 'daily'), 'ThetaData end-of-day options for 15 ETFs; source: data/options.sqlite'),
    CollectorDefinition('quant-data-theta-options-weekly.timer', 'Theta options weekly repair', (
        'options.theta.receipts',
        'options.theta.selected_contracts',
        'options.theta.daily_research',
    ), ('--mode', 'weekly'), 'Repair missing ThetaData options for the preceding trading week; source: data/options.sqlite'),
    CollectorDefinition('quant-data-macro-current-refresh.timer', 'Macro current', (
        'fixture.macro.rtdsm_employ_evidence',
        'fixture.macro.rtdsm_employ',
        'fixture.macro.stage3_catalog',
        'fixture.macro.treasury_yield_curves',
        'fixture.macro.soma_evidence',
        'fixture.macro.soma_summary',
        'macro.eia.electricity_retail_history',
        'macro.eia.electricity_retail_history_evidence',
        'macro.eia.petroleum_weekly_stock_history',
        'macro.eia.petroleum_weekly_stock_history_evidence',
    ), (), 'Treasury, Fed, NY Fed, energy and economic indicators'),
    CollectorDefinition('quant-data-macro-vintages.timer', 'GDP / CPI', (
        'macro.official_vintages',
        'macro.official_vintages_evidence',
    ), ('--mode', 'refresh'), 'Official GDP and inflation vintages'),
    CollectorDefinition('quant-data-employment-vintages.timer', 'Employment', (
        'macro.official_vintages',
        'macro.official_vintages_evidence',
    ), ('--mode', 'refresh'), 'Payroll and unemployment vintages'),
    CollectorDefinition('quant-data-fmp-macro-calendar.timer', 'Economic calendar', (
        'fixture.macro.economic_calendar',
        'macro.fmp.economic_calendar_incremental_evidence',
        'macro.fmp.economic_calendar_incremental_events',
    ), (), 'Release calendar and normalized economic events'),
    CollectorDefinition('quant-data-sec-company-fundamentals.timer', 'SEC fundamentals', (
        'fixture.company.sec_evidence',
        'fixture.company.issuers',
        'fixture.company.filings',
        'fixture.company.fundamentals',
        'fixture.company.filing_issuer_membership',
    ), (), 'Company filings and financial facts'),
    CollectorDefinition('quant-data-sharadar-selected-refresh.timer', 'Sharadar fundamentals', (
        'company.sharadar.evidence',
        'company.sharadar.sf1',
        'company.sharadar.definition_evidence',
        'company.sharadar.definitions',
    ), (), 'Selected company fundamentals, as reported and restated'),
    CollectorDefinition('quant-data-company-market-refresh.timer', 'Company market', (
        'fixture.company.action_evidence',
        'fixture.company.corporate_actions',
        'fixture.company.expectation_evidence',
        'fixture.company.expectations',
    ), (), 'Dividends, splits and analyst estimates'),
    CollectorDefinition('quant-data-selected-company-refresh.timer', 'Selected company inputs', (
        'company.fmp.research_evidence',
        'company.fmp.research_inputs',
        'company.fmp.analyst_evidence',
        'company.fmp.analyst_observations',
    ), (), 'Earnings, financial statements, estimates, ratings, price targets and revenue segments'),
    CollectorDefinition('quant-data-equibles-refresh.timer', 'Equibles transcript refresh', (
        'company.equibles.transcripts',
    ), (), None),
    CollectorDefinition('quant-data-equibles-transcripts.timer', 'Equibles transcripts', (
        'company.equibles.transcripts',
    ), (), 'Raw earnings-call transcripts for the retained stock universe'),
    CollectorDefinition('quant-data-current-news-refresh.timer', 'Current news', (
        'news.fmp.stock_latest_current_evidence',
        'news.current_multi_source_evidence',
    ), (), 'Headlines from the configured current news sources'),
    CollectorDefinition('quant-data-weekly-price-repair.timer', 'Weekly price repair', (
        'market.stage10.daily_prices',
        'market.stage10.source_evidence',
    ), (), 'Repair missing daily price history'),
    CollectorDefinition('quant-data-derived-refresh.timer', 'Derived calculations', (
    ), (), 'Forward EPS, fiscal-quarter matching and daily P/E from retained inputs'),
)

TIMER_BINDINGS = tuple(TimerBinding(c.unit, c.label, c.datasets) for c in COLLECTORS)
FETCH_DESCRIPTIONS = {c.label: c.description for c in COLLECTORS if c.description is not None}
# Keep the historical Alpaca receipt decoder without reinstating a timer binding.
ARCHIVED_BATCH_ARGUMENTS = {"quant-data-alpaca-spy-options.timer": ()}
BATCH_ARGUMENTS = {**{c.unit: c.arguments for c in COLLECTORS}, **ARCHIVED_BATCH_ARGUMENTS}
