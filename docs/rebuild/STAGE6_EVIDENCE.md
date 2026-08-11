# Stage 6 Acceptance Evidence

Status: Bounded offline portal accepted; browser-automation check explicitly
waived by the user
Gate date: 2026-08-10
Scope: bounded offline four-route local portal only

## 1. Boundary

Stage 6 establishes a local, read-only portal over the reviewed synthetic
four-store cohort. Its frozen registry projection is revision `2.4.0`, schema
`1.2.0`. It declares exactly four ordered local-private dashboard exposures:

- `stage1.overview` at `/`;
- `stage6.gdp_vintages` at `/gdp-vintages`;
- `stage6.table_inspector` at `/table-inspector`; and
- `stage6.agent_tools` at `/agent-tools`.

The portal uses fixed registry-owned datasets, relations, filters,
server-owned sort fields, pagination, tool dependencies, and API routes. It
does not accept raw SQL, a SQLite URI, a database path, or a caller-selected
relation. Reads use explicit four-store routing and query-only connections.

This is a bounded portal, not the complete historical UI surface. Charts,
indicator research, algorithms, walk-forward/backtest reports, exports, Atlas,
hosting, promotion, live providers, scheduler installation, and destructive
storage operations remain outside the implemented scope. The Stage 5 rebuild
uses its historical `2.3.0`/`1.1.0` registry projection to preserve its
approved evidence; the Stage 6 portal uses its frozen `2.4.0`/`1.2.0`
projection.

## 2. Frozen artifacts and pins

- Frozen Stage 6 registry projection from
  [the canonical registry loader](../../quant_data/registry.py): revision
  `2.4.0`, schema `1.2.0`, 30 migrations, 33 datasets, 19 collectors,
  57 tools, four dashboard exposures, zero jobs, and zero exports.
- [Current canonical registry](../../config/system_registry.json): Stage 7
  revision `2.5.0`, schema `1.3.0`; the Stage 6 projection preserves this
  evidence without changing any Stage 6 pin.
- [Stage 6 application](../../quant_data/dashboard/application.py) and
  [read services](../../quant_data/dashboard/read_services.py).
- Local [CSS](../../quant_data/dashboard/static/dashboard.css),
  [JavaScript](../../quant_data/dashboard/static/dashboard.js), and
  [Inter variable font](../../quant_data/dashboard/static/inter-variable.woff2).
- [Inter OFL 1.1 license](../../quant_data/dashboard/licenses/INTER-OFL-1.1.txt).
- [Stage 6 golden](../../tests/fixtures/stage6_golden.json) and
  [two-root integration gate](../../tests/test_stage6_integration.py).

The deterministic pins are:

- Stage 5 evidence: `c099d0690464a84035c9dd05944da6193a3b019f540f32b2acfe6bc5454f2d6a`;
- portal snapshot: `c216de35059e7cf554f5e64ecd2c887207763c7f90b34761ddfc25d9484d8454`;
- local `dashboard.js`: `526aa89703955629e089a0fd4dceac55ca980c1b05f65ec2ee6921c716a96e3c`;
- Inter variable font: `693b77d4f32ee9b8bfc995589b5fad5e99adf2832738661f5402f9978429a8e3`;
- Inter OFL 1.1 license:
  `262481e844521b326f5ecd053e59b98c8b2da78c8ee1bdbb6e8174305e54935a`; and
- Stage 6 evidence:
  `868b84691629de835c05219355e0741e3691cd6e34f502abe59e0790d2573794`.

Inter is the local `InterVariable.woff2` asset from the
[official Inter 4.1 release](https://github.com/rsms/inter/releases/tag/v4.1),
distributed with its included SIL Open Font License 1.1 text. This records
asset provenance only. The visual direction is ThesisTrade-inspired, not
copied, and no ThesisTrade asset, font, script, stylesheet, logo, wording, or
source is used.

## 3. Reproduction and primary-gate result

From the canonical WSL project root:

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -m unittest discover -s tests -t . -v
```

Result: `Ran 228 tests in 139.235s — OK`.

The Stage 6 integration gate uses two independent, explicit empty roots:

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -B -m unittest \
  tests.test_stage6_integration -v
```

Result: `2/2` integration checks passed. The gate rejects a nonempty explicit
work root without altering its contents. Its clean rebuild command is:

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -B -m quant_data \
  --stage stage6 \
  --project-root /home/volatility/Python_Projects/Quant_Data_Infra \
  --store-root <empty-work-root-1> \
  --second-store-root <empty-work-root-2>
```

It rebuilds the frozen Stage 5 cohort under each root, opens source and
restored stores through the frozen Stage 6 registry projection, and requires
byte-identical path-free evidence.

## 4. Primary-gate checks

The primary gate proves that:

- all four fixed pages share persistent navigation and local CSS/JavaScript;
- registered GDP-vintage, table-inspector, health, price-series, and
  agent-tool APIs return strict JSON through fixed read services;
- hostile relation, SQL, path, sort, pagination, method, and unknown-route
  attempts fail closed;
- pages, local assets, and errors receive the reviewed loopback security
  headers;
- the 57-tool Agent Tools surface preserves explicit `not_established` and
  capability-unavailable states rather than inventing unsupported behavior;
- source and restored store mutation fingerprints are unchanged before and
  after the portal requests;
- the portal cannot reach initialization, migration, ingestion, or
  writer-connection entry points;
- source and restored portal snapshots match, the route set is fixed, and the
  result is loopback-only;
- the frozen Stage 6 registry projection declares zero jobs and zero exports; and
- the evidence records zero runtime network assets.

These checks use only reviewed synthetic fixtures and explicit temporary roots.
They do not fall back to default or live database paths.

## 5. Independent checks, waiver, and gate state

The bounded offline portal gate is **accepted**. Independent read-only
verification passed the 228-test suite, deterministic two-root CLI, registry
and query-contract tamper probes, strict-JSON runner checks, read-only store
fingerprints, local-asset pins, and focus-contrast calculation. The Codex
browser controller repeatedly failed before attaching to the visibly running
loopback portal, so responsive, keyboard, reduced-motion, 200% zoom, and
runtime browser-network automation could not be collected.

On 2026-08-10 the user explicitly accepted that browser-automation limitation,
authorized this Stage 6 commit, and authorized bounded offline Stage 7 work.
This is a waiver, not a claim that the unavailable browser automation passed.
The formal chart, indicator, algorithm, and backtest requirements remain
deferred, and Stage 8 export/Atlas work remains closed.

The prior independently verified platform state is recorded in the
[Stage 5 acceptance evidence](STAGE5_EVIDENCE.md).
