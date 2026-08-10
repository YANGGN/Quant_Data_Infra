# Stage 5 Acceptance Evidence

Status: Fixture-validated and independently verified
Gate date: 2026-08-10
Scope: offline composable 57-tool platform only

## 1. Boundary

Stage 5 implements the typed read-only tool core described by the
[tool-platform contract](TOOL_PLATFORM_SPEC.md). Registry revision `2.3.0`
declares exactly 57 public names, generated input/output schemas, fixed routing
and workload bounds, no jobs, and no exports. The live intraday name remains
registered but is capability-disabled in this offline stage.

The 55 newly exposed names use explicit `forward_reconstructed_v1` contracts;
the lost byte-exact historical schemas were not recovered. A result is
`not_established` when the synthetic fixtures do not justify the requested
composite or statistic. This is an explicit unavailable state, not fabricated
data or a silent fallback.

## 2. Frozen artifacts

- [Canonical registry](../../config/system_registry.json): registry `2.3.0`,
  schema `1.1.0`, 57 tools, zero jobs, zero exports.
- [Generated schema catalog](../../quant_data/generated/tool_contract_schemas_v1.json):
  `a2469c903cc6c9dae64ea29c4d3b543837a37d4989277290220061101d28de87`.
- [Approved Stage 5 golden](../../tests/fixtures/stage5_golden.json).
- [Stage 5 integration gate](../../tests/test_stage5_integration.py).

The deterministic evidence pins are:

- Stage 4 evidence: `c5759ca09c3fccd8f5c79c51131c5456c0357b928993dd6182417dba919aa7e7`;
- manifest: `baf5c67f146372e93b4433cedd6f7e1723a378015606e9b5767ca89f60973008`;
- tool matrix: `352cb6ff91d509802fa6348a7ad47bca8b2c33334c9c6db6686e2d0ebfa449ce`;
- Stage 5 evidence: `c099d0690464a84035c9dd05944da6193a3b019f540f32b2acfe6bc5454f2d6a`.

The two-root CLI emitted one 1,827-byte strict-JSON line, zero stderr bytes,
and stdout SHA-256
`c4f4f43323353a1106bd43597ad2b22d3c55071c64313fd233efa7829ec22e04`.
Its result classes were six `ok`, two `succeeded`, 48 `not_established`, and
one capability-disabled intraday result.

## 3. Reproduction

From the canonical WSL project root:

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -B -m unittest \
  tests.tool_platform.test_arguments \
  tests.tool_platform.test_results \
  tests.tool_platform.test_analytics \
  tests.tool_platform.test_analytic_adapter \
  tests.tool_platform.test_domain_operations \
  tests.tool_platform.test_catalog_dispatch -v
```

Result: 40 tests passed.

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -B -m unittest \
  tests.boundary.test_stage1_boundary \
  tests.test_core \
  tests.test_registry_identifiers \
  tests.test_stage4_migrations -v
```

Result: 47 tests passed.

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -B -m unittest \
  tests.test_stage5_integration -v
```

Result: 3 tests passed. The gate runs every public tool through direct and HTTP
dispatch, fingerprints source/restored stores, and instruments initialization,
migration, ingestion, and writer-connection entry points to prove they are
unreachable from tool execution.

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -B -m unittest \
  discover -s tests -t . -v
```

Result: 207 tests passed in 118.509 seconds.

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -B -m quant_data \
  --stage stage5 \
  --project-root /home/volatility/Python_Projects/Quant_Data_Infra \
  --store-root <empty-work-root-1> \
  --second-store-root <empty-work-root-2>
```

The CLI refuses nonempty roots and requires byte-identical path-free evidence
from both clean rebuilds.

## 4. Primary-gate findings closed

The integrated gate verifies:

- input and output schemas are generated from immutable typed declarations;
- schema validation and deterministic cost preflight occur before typed series
  construction or domain work;
- transformations return composable, lineage-bound `TimeSeries` values;
- deadlines, host cancellation, concurrency, output size, and strict finite
  JSON fail with distinct bounded errors;
- successful and rejected named dispatches emit sanitized receipts with exact
  input/output schema hashes and no values, paths, SQL, or secrets;
- HTTP and in-process calls are equivalent for all 57 registered names;
- all tool reads remain mutation-neutral; and
- the historical Stage 1 two-tool projection remains valid.

## 5. Independent verification

A read-only SolUltra verifier independently reproduced the 207-test full suite,
the fresh two-root CLI with all exact pins, all 57 direct and loopback HTTP
routes, hostile-input and host-boundary failures, and total store-mutation
neutrality. The independent full suite passed in 117.719 seconds.

The verifier identified and then confirmed closure of two bounded findings:
the Stage 1 Overview retains its fixed `Two-tool manifest` heading, leaving
57-tool UI work to Stage 6, and the architecture now describes the current
Stage 5 public boundary accurately. The correction gate passed 19 tests in
28.798 seconds, and every pre/post workspace seal value matched.

## 6. Gate state and exclusions

The primary fixture gate and independent SolUltra verification are **passed**;
`G5` is complete. Stage 5 does not authorize live providers, network
collection, scheduler installation, exports, promotion, hosting, destructive
operations, or UI implementation.

This verified Stage 5 snapshot must be committed separately before Stage 6
executable work begins. That commit opens Stage 6 as the next authorized stage.
The prior stage evidence remains in
[Stage 4 acceptance evidence](STAGE4_EVIDENCE.md).
