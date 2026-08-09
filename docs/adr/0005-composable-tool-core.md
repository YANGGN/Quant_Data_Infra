# ADR 0005: Implement Public Tools from a Composable Typed Core

## Status

Accepted

## Context

Recovery evidence identifies a 57-name, read-only public tool surface with
fixed input and output schemas. Rebuilding each public name as an independent
function would duplicate query routing, availability logic, time-series
transformations, return definitions, statistics, lineage, validation, and
resource controls. Those copies would drift and make a public compatibility
surface the implementation architecture.

The platform must also support in-process composition: a series selected once
must be usable by transformation, description, stationarity, regression,
alignment, and correlation without losing time, missingness, provenance, or
return semantics in a JSON round trip.

Related documents:

- [Architecture](../../ARCHITECTURE.md)
- [Tool platform specification](../rebuild/TOOL_PLATFORM_SPEC.md)
- [System registry specification](../rebuild/SYSTEM_REGISTRY_SPEC.md)
- [Data and time contracts](../rebuild/DATA_AND_TIME_CONTRACTS.md)
- [Roadmap](../../ROADMAP.md)

## Decision

The rebuilt platform will implement public tools as versioned compatibility
adapters over a shared immutable typed core.

The user accepted 57-name compatibility on 2026-08-09. All recovered names
will remain reserved for their versioned contracts. Stage 1 exposes only its
two declared milestone tools and does not claim full compatibility. The name
count does not imply 57 separate analytical implementations.

The architecture will contain:

1. a deterministic system registry that binds a public name and generated
   schemas to one operation graph;
2. strict public adapters that parse, validate, apply compatibility defaults,
   and adapt typed results;
3. a host-created execution context with read-only store handles, deadlines,
   budgets, and observability;
4. shared typed store-query, time-series, transformation, statistical,
   research, and lineage primitives; and
5. post-execution typed and JSON-schema validation.

Typed values, not unstructured dictionaries, are the internal composition
contract. Public JSON remains a boundary representation. Database paths and
SQL remain host-controlled and cannot be supplied by a caller.

Input and output schemas will be generated from the same typed definitions used
at runtime. A semantic change to availability, units, missingness, returns,
execution timing, estimator choice, or truncation requires an explicit version
even when the JSON shape does not change.

## Consequences

### Positive

- One implementation governs each semantic operation.
- Public compatibility can be retained without freezing internal module shape.
- In-process composition preserves type, time, lineage, and research contracts.
- Schema, examples, limits, dispatch, and documentation can be checked against
  one registry.
- Output validation and deterministic JSON become uniform across all tools.
- Primitive changes have an enumerable public blast radius.

### Negative

- Registry and schema-generation infrastructure must exist before broad tool
  restoration.
- Compatibility adapters add a maintained boundary layer.
- Typed result design requires deliberate treatment of matrices, diagnostics,
  warnings, truncation, and heterogeneous domain records.
- A shared primitive defect can affect several public tools, so golden tests and
  impact mapping are mandatory.

### Risks and mitigations

- **Risk:** a compatibility adapter hides a semantic change. **Mitigation:**
  schema fixtures plus explicit semantic versions and recovered parity tests.
- **Risk:** generic primitives erase domain provenance. **Mitigation:** domain
  gateways construct typed values with source-specific lineage before generic
  transformations run.
- **Risk:** public JSON is trusted as an internal object. **Mitigation:** only
  explicitly versioned composable types may be decoded, and they are fully
  revalidated.
- **Risk:** a registry becomes dynamic code execution. **Mitigation:** operation
  graphs are server-owned identifiers; caller import paths, reflection, SQL, and
  `eval` are prohibited.

## Alternatives

### Rebuild one independent implementation per public tool

Rejected because it duplicates the highest-risk semantics and makes drift
likely.

### Replace the named surface with one generic SQL or dataframe tool

Rejected because it defeats read-only allowlisting, resource bounds, semantic
contracts, and compatibility.

### Expose only a small new primitive API

Rejected because the accepted compatibility target preserves all 57 recovered
names while allowing staged implementation over the smaller core.

### Compose tools through serialized JSON only

Rejected because it is lossy, inefficient, and encourages callers to bypass
unit, time, availability, and lineage validation.

## Acceptance evidence

This decision is accepted only when:

- the accepted 57-name decision and each milestone subset are recorded;
- the canonical registry has exactly the accepted public names with generated
  strict schemas and one resolvable operation graph each;
- no public adapter contains SQL or analytical formulas;
- primitive-to-tool impact tests enumerate all bound public names;
- in-process composition preserves time, units, missingness, provenance,
  research contracts, and return-target contracts;
- HTTP and in-process results are semantically equivalent;
- four distinct split-store tests prove host-selected read-only routing and
  duplicate physical paths fail closed;
- output-schema and finite-JSON validation fail closed; and
- recovered compatibility fixtures or documented conflict resolutions pass.
