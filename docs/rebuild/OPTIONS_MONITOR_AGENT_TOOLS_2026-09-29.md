# Intraday Options Monitor agent tools

Implemented September 29, 2026. Registry **2.95.0**, catalog **2.39.0**:
100 logical tools / 196 versioned contracts. Both additions use API `1.0`
and explicit tool version `1.0.0`.

These read-only tools expose the existing derived Options Monitor observations
through the shared local launcher. They do not collect data, change watchlists,
contact providers, or change the monitor schedule. They are periodic saved
aggregates, not streaming quotes, full option-chain snapshots, or contract bars.

## Interface

| Tool | Required arguments | Optional arguments |
| --- | --- | --- |
| `options.get_intraday_snapshot` | `symbols`: 1–50 unique uppercase symbols | `session`, `as_of` |
| `options.get_intraday_history` | `symbol`, `start_date`, `end_date` | `as_of` |

Dates are exact ISO calendar dates in the monitor's America/New_York session
calendar. History ranges are inclusive and limited to seven calendar days,
with at most 1,000 observations. Split longer requests into disjoint date ranges.
Exceeding a bound fails explicitly; there is no silent truncation or pagination.

Snapshot selects the greatest eligible capture instant per symbol, with session
and scheduled slot as deterministic tie breakers. Without `session`, this can
be a previous session's observation: always inspect session and age. Supplying
`session` requires that exact date and does not fall back to another session.
History is ordered by capture instant, session, then scheduled slot.

`as_of` is an optional timezone-aware ISO instant, inclusive at microsecond
precision. It defaults to the host clock and cannot be in the future. It filters
local capture time, not original public availability or an independently
recorded transaction-commit time. The current configured roster gates symbol
support; this does not establish historical universe membership.

## Calls from another project

Use the absolute launcher, without importing this repository or opening SQLite:

```bash
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools describe options.get_intraday_snapshot --tool-version 1.0.0
/home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call <<'JSON'
{"api_version":"1.0","tool":"options.get_intraday_snapshot","tool_version":"1.0.0","arguments":{"symbols":["SPY","QQQ","AAPL"]}}
JSON
```

History example (dates are explicit examples, not a latest-data assumption):

```json
{"api_version":"1.0","tool":"options.get_intraday_history","tool_version":"1.0.0","arguments":{"symbol":"SPY","start_date":"2026-09-28","end_date":"2026-09-29","as_of":"2026-09-29T16:00:00-04:00"}}
```

Windows agents send that JSON on standard input to:

```powershell
wsl.exe -d Ubuntu -- /home/volatility/Python_Projects/Quant_Data_Infra/bin/quant-data-tools call
```

## Output and interpretation

The standard response envelope preserves receipts, warnings, research metadata,
and explicit truncation state. Read `result.records` as `record_type` and
name/value `fields`; decode fields ending in `_json` once.

- `monitor_source`: provider, derived source identity and schema checksum,
  reader version, effective capture cutoff, read time, selected high-water
  capture, counts, retention, selection rule, and zero provider requests.
  `source_high_water` covers the returned observations only.
- `monitor_observation`: saved call/put and interval volumes, volume ratios,
  OI and its effective date, IV estimates, leaders, baseline count, coverage,
  quality flags, and all available documented aggregate fields. Missing fields
  stay null; incomplete newest observations are returned rather than replaced
  with older complete data.
- `monitor_missing`: `outside_current_monitor_universe` or
  `no_saved_observation_at_cutoff`. The latter does not distinguish collection
  gaps, pruned history, pre-cutoff absence, and market closures. It does not
  mean zero activity.

Each observation includes the original `captured_at`, its `scheduled_at`,
saved `cadence_minutes`, an identity hash of session/symbol/slot, the exact
stored payload SHA-256, and the contract-population fingerprint.
`age_seconds` uses the read clock; `age_at_cutoff_seconds` uses the effective
cutoff. `freshness_at_cutoff` is `within_cadence` when elapsed age is no more
than the saved cadence and `older_than_cadence` otherwise. This is a transparent
elapsed-time comparison, not a calendar-aware collector health assessment or
a promise of synchronized quote freshness.

IV is a provider-derived proxy. OI retains its own effective date and is not
intraday positioning. Interval values and flags are passed through from the
monitor's accepted calculation contract; no delta is reconstructed across
missing observations. Baselines may still be warming up. Preserve warnings,
source records, hashes, dates and quality flags downstream.

The result is `ok` if at least one saved observation is returned and
`not_established` otherwise. This status establishes record presence, not
metric completeness, quote quality, or live readiness. Historical public
availability remains `not_established`.

## Source, bounds and access

The sole data source is the host-fixed
`data/.operations/options-monitor/monitor.sqlite`, using the current
`config/options_monitor_universe.json` roster. The store is a derived research
materialization with rolling 90 observed-session retention; raw inputs are not
retained. Typical collection targets are 5/10/15/30 minutes, subject to the
existing collector's capacity and normal clock execution.

Access reuses `MonitorStore.read()` under the resolved physical store lock.
The source must already exist; tools never initialize it. Readers use
`mode=ro&immutable=1`, validate schema identity, reject unsafe aliases and
nonempty journals/WAL, and check file identity around the read. Acquiring the
existing lock may create lock metadata, but no database bytes are changed.
Missing, busy, or invalid sources produce `store_unavailable`, with no
canonical daily-store, legacy Alpaca, provider, or Site fallback.

Calls are bounded by a five-million SQLite operation budget, cancellation and
deadline checks, 64 KiB per saved payload, 8 MiB aggregate decoded payloads and
the existing 8 MiB response ceiling. Database paths, SQL, provider selection,
credentials and writable connections are not public arguments. Only documented
aggregate fields cross the boundary; request receipts, delivery state and
private connection material are excluded.

The [monitor contract](OPTIONS_MONITOR_2026-09-26.md) owns collection and
metric semantics. [Local Agent Tools](../LOCAL_AGENT_TOOLS.md) owns integration.
Offline verification uses synthetic temporary stores; it does not establish
current host collection freshness.
