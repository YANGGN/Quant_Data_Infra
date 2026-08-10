# UI Visual Direction

Status: Accepted

Implementation status: Not implemented; this contract governs Stage 6 and
Stage 8 UI work

Reference reviewed: 2026-08-09 - [ThesisTrade](https://www.thesistrade.ai/)

## 1. Decision

The rebuilt local dashboard and Quant Data Atlas will use a shared visual
language inspired by ThesisTrade: restrained, institutional, calm, and
data-first. The defining cues are Inter typography, a cool off-white canvas,
white work surfaces, dark slate text, thin borders, compact radii, subtle
shadows, generous whitespace, and sparing semantic color.

This is a design-direction reference, not permission to copy ThesisTrade's
logo, name, wording, illustrations, screenshots, source code, or proprietary
assets. The implementation must express the Quant Data Infrastructure's own
identity and information architecture. It must not load CSS, fonts, images, or
scripts from the reference site at runtime.

The local dashboard introduces the shared tokens and application shell in
Stage 6. Atlas consumes the same tokens and interaction vocabulary in Stage 8.
Their data boundaries remain different even though they look like one product.

## 2. Desired feel

The interface should feel like a private institutional research workspace:

- serious without looking heavy or austere;
- spacious at the page level and compact inside analytical surfaces;
- precise, quiet, and trustworthy rather than promotional;
- structured by alignment, typography, and borders before color or effects;
- comfortable for long research sessions; and
- clearly designed for evidence, provenance, and decision support.

Avoid the styling of a generic admin template, consumer trading app, crypto
terminal, or neon dark-mode dashboard. In particular, avoid decorative
gradients, glassmorphism, oversized rounded cards, excessive pills, glowing
charts, dense rainbow palettes, dramatic shadows, and animation without an
information purpose.

## 3. Shared visual tokens

These are the accepted starting tokens. They deliberately track the reference
site's visual proportions and core palette while giving semantic meaning to
the Quant Data Infrastructure UI.

### 3.1 Color

| Token | Value | Use |
| --- | --- | --- |
| `canvas` | `#f8f9fa` | Page and application background |
| `surface` | `#ffffff` | Primary panels, cards, tables, and dialogs |
| `surface-soft` | `#fafafa` | Subtle grouped regions and inactive controls |
| `text-primary` | `#0f172a` | Headings, primary values, active navigation |
| `text-secondary` | `#475569` | Supporting labels and descriptions |
| `text-muted` | `#64748b` | Metadata, timestamps, and secondary context |
| `border` | `#e2e8f0` | Standard 1 px dividers and component outlines |
| `border-soft` | `#f1f5f9` | Quiet row and nested-region separation |
| `focus` | `#94a3b8` | Visible keyboard focus ring |
| `positive` | `#047857` | Positive financial or healthy state only |
| `positive-soft` | `#a7f3d0` | Positive tint, never primary body text |
| `negative` | `#be123c` | Negative financial or failed state only |
| `negative-soft` | `#fecdd3` | Negative tint, never primary body text |

Slate is the product accent. Emerald and rose are semantic signals, not brand
decoration. Warnings, stale data, missing values, and partial results must have
their own accessible label or icon and must never rely on color alone. A future
dark theme is a separate decision; Stage 6 must not delay the accepted light
theme to build one.

### 3.2 Typography

- Primary family: `Inter` at weights 400, 500, 600, and 700.
- Runtime delivery: self-hosted or bundled with the application; no Google
  Fonts or other external font request is allowed from the local UI or Atlas.
- Fallback stack: `-apple-system`, `BlinkMacSystemFont`, `Segoe UI`, and
  `sans-serif`.
- Body text: 14-16 px with a comfortable 1.45-1.6 line height.
- Navigation and controls: 14 px, weight 500.
- Supporting metadata: 11-12 px, weight 500-600; uppercase is reserved for
  short kickers, state labels, and panel labels.
- Page headings: responsive, approximately 32-48 px, weight 600-700, with
  restrained negative letter spacing.
- Panel headings: 18-24 px, weight 600.
- Dense financial values and table columns use tabular numerals.

Inter is part of the intended feel, not an optional embellishment. A font-load
failure must still produce a usable interface through the declared fallback
stack without shifting controls out of their bounds.

### 3.3 Shape, depth, and spacing

- Radius scale: 4 px for controls and badges, 6 px for panels, and 8 px for
  application shells or large composed surfaces.
- Fully rounded pills are limited to compact status, range, or segmented
  controls; ordinary cards and buttons are not pills.
- Standard shadow: `0 1px 2px rgba(15, 23, 42, 0.08)`.
- Elevated shell shadow: `0 18px 40px rgba(15, 23, 42, 0.10)`.
- Most hierarchy should come from whitespace and 1 px borders. Elevated
  shadows are reserved for the primary workspace shell, menus, and dialogs.
- Use a 4 px base spacing rhythm. Prefer 8, 12, 16, 24, 32, 48, and 64 px
  increments for composed layouts.
- Content width should remain comfortable on large displays, with a default
  maximum around 1280 px unless a wide data table or chart explicitly needs
  more room.

## 4. Layout and component language

### 4.1 Application shell

Use a stable, low-profile navigation shell with clear active state and quiet
chrome. Persistent navigation must remain visible on every implemented page.
The main workspace should read as one coherent surface rather than a mosaic of
unrelated floating cards.

Desktop research views may use a slim left rail, a top bar, or both when the
information architecture justifies them. Navigation structure must be shared
between the dashboard and Atlas where destinations overlap, while unavailable
or boundary-specific destinations are omitted rather than shown as broken.

### 4.2 Panels and cards

- Use white panels on the cool canvas with thin slate borders.
- Keep panel headers small and information-dense: label, title, provenance or
  cutoff, and at most the actions appropriate to that surface.
- Reserve metric cards for values that deserve comparison or monitoring; do
  not wrap every paragraph or field in a card.
- Hover elevation should be subtle and used only when a surface is actually
  interactive.
- Empty, unavailable, partial, stale, and error states keep the same layout so
  the interface does not jump or imply that missing data is zero.

### 4.3 Tables, filters, and forms

- Tables are the primary detailed-data surface: crisp row dividers, restrained
  headers, tabular numerals, predictable alignment, and bounded density.
- Filters live in a quiet toolbar and use labels that remain visible after a
  selection. The UI must expose the active availability cutoff wherever it
  affects results.
- Provenance, warnings, exclusions, truncation, and freshness remain distinct
  fields or sections rather than being compressed into a generic status icon.
- Destructive, ingestion, raw-SQL, arbitrary-path, and arbitrary-relation
  controls do not exist in either read-only UI.

### 4.4 Charts and data graphics

- Default series and axes use slate; emerald and rose indicate meaningful
  positive and negative states.
- Grid lines and reference rules use the border palette and stay visually
  subordinate to the data.
- Missing observations render as gaps. No visual layer may interpolate a value
  that the underlying contract does not provide.
- Comparison series must remain distinguishable without color alone through
  labels, markers, line patterns, or direct annotation.
- Tooltips show effective time, available time or cutoff, value, unit, and
  relevant provenance when those fields exist.
- Charts should favor analytical legibility over decorative volume, glow,
  gradients, or faux depth.

## 5. Interaction and motion

- Standard transitions should be approximately 150-220 ms and limited to
  color, border, opacity, or small purposeful movement.
- Do not animate financial values in a way that obscures their exact state.
- Keyboard focus must always be visible.
- Support `prefers-reduced-motion`; disabling motion must not remove meaning.
- Loading states preserve the final layout and use restrained skeletons or
  progress text. They never fabricate example values.
- Every control must have a clear hover, focus, active, disabled, loading, and
  error state where applicable.

## 6. Dashboard and Atlas application

| Concern | Stage 6 local dashboard | Stage 8 Quant Data Atlas |
| --- | --- | --- |
| Shared style | Establishes tokens, typography, navigation, panels, tables, charts, and state treatments | Reuses the same tokens and component language |
| Primary feel | Live private research and operational inspection workspace | Curated private analytical publication |
| Data source | Fixed read services over query-only operational stores | Versioned static manifest and snapshot chunks only |
| Freshness treatment | Prominent live-store health, availability cutoff, and per-query receipts | Prominent snapshot identity, generation time, freshness, and source revision |
| Actions | Bounded read and research interactions only | Static exploration, filtering, and local presentation only |

Visual consistency must not blur the architecture boundary. Atlas must never
gain a live-store connection merely to share a dashboard component, and the
dashboard must never read an Atlas export as an undeclared canonical source.

## 7. Responsive and accessibility contract

- Design desktop-first for a research workstation, then verify at representative
  desktop, tablet, and narrow mobile widths.
- Dense tables may use deliberate horizontal scrolling with sticky identity
  columns; they must not silently hide fields.
- Navigation collapses without losing the current location or keyboard access.
- Text and functional controls meet WCAG 2.2 AA contrast targets.
- Structure uses semantic landmarks, heading order, table semantics, labels,
  and meaningful accessible names.
- Status and chart meaning are never communicated by color alone.
- Zoom to 200% must remain usable without clipped controls or inaccessible
  content.

## 8. Implementation constraints

- Define the accepted tokens once as versioned CSS custom properties or an
  equivalent framework-independent token module.
- Dashboard and Atlas consume that single source; neither keeps a divergent
  copied palette.
- Icons use one restrained outline family, such as Lucide, with accessible
  labels where the icon is not decorative.
- All production assets are local, pinned, and reproducible. The browser must
  not fetch reference-site assets, remote fonts, analytics, or trackers.
- The `.vite` cache is not application source and must not be used as a basis
  for recovering the previous UI.
- This contract does not select a UI framework, package manager, hosting
  provider, or Atlas deployment target.

## 9. Acceptance evidence

Stage 6 UI work is not complete until:

1. the implemented token set matches this accepted contract;
2. Inter is locally available in production builds and the fallback layout is
   tested;
3. Overview, GDP Vintages, Tables, and Agent Tools share the same shell and
   state treatments;
4. visual-regression fixtures cover representative desktop, tablet, and mobile
   widths, plus empty, loading, partial, warning, error, and dense-data states;
5. keyboard navigation, visible focus, reduced motion, contrast, and 200% zoom
   checks pass;
6. browser network tests prove that no remote font or reference-site asset is
   requested; and
7. the existing strict read-only and all-store mutation-fingerprint gates pass.

Stage 8 Atlas UI work additionally requires:

1. Atlas consumes the same versioned visual tokens as the dashboard;
2. visual fixtures cover snapshot identity, staleness, provenance, truncated
   data, unavailable datasets, and the last-valid-snapshot failure state;
3. the static build works without an operational-store connection; and
4. visual or component reuse introduces no network, write, or publication
   boundary bypass.

The external reference is a point-in-time design cue. Changes to the reference
site do not silently change this contract; altering these tokens or the desired
feel requires an explicit documentation update and review.
