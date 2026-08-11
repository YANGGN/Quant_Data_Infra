# Quant Data Atlas static shell

This directory contains the dependency-free, static Stage 8 Atlas shell. It is
source only: it has no package manifest, build output, hosting configuration,
operational-store connection, live API client, or deploy command.

The page loads only these relative publication assets:

- `assets/dashboard.css` — the pinned Stage 6 shared token source;
- `assets/inter-variable.woff2` — the locally bundled Inter font;
- `assets/atlas.css` and `assets/atlas.js` — this Atlas shell; and
- `data/manifest.json` plus the manifest-declared `data/...` JSON chunks.

The exporter owns the generated `data/` tree. It must copy this shell and the
Stage 6 local CSS/font/OFL assets into its exact static publication staging
tree. This source directory deliberately does not contain a generated snapshot.

## Public manifest contract

The static loader accepts a bounded strict-JSON `data/manifest.json` with these
public fields:

```text
export_id
revision_id
generated_at
cutoff
registry
source_stores
cross_store_atomic
datasets[]: id, label, state, completeness, freshness, schema,
            chunks[]: path, sha256, rows, bytes
```

`cross_store_atomic` must be `false`. Each chunk `path` must be a safe relative
path below `data/`; URL schemes, absolute paths, traversal, backslashes, empty
segments, and unsafe characters are rejected. Chunk payloads may be a JSON row
array or an object with a `rows` array.

Before rendering, the loader rejects duplicate JSON object keys, malformed or
non-finite numbers, unpaired Unicode surrogates, excess depth, large metadata,
unsafe paths, too many datasets/chunks/rows/bytes, mismatched chunk byte or row
counts, and a SHA-256 mismatch. SHA-256 validation uses browser WebCrypto. The
DOM renderer uses only text nodes and created elements, so public metadata and
row values cannot become markup.

The loader never reaches an operational store, API route, arbitrary URL, or
user-selected path. If a reload fails after a verified manifest is shown, the
current in-page last valid snapshot stays visible and the failure is labeled;
Atlas never falls back to live data.

## Local validation

Run the focused dependency-free test from the repository root:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.atlas.test_static_site -v
```

When the WSL-native Node runtime is present, the test also performs syntax and
behavior checks for strict JSON, path validation, manifest limits, and
WebCrypto chunk verification.
