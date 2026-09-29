# Equibles incremental transcript refresh

Status: activated September 25, 2026 at 09:06 EDT after explicit user approval.
The daily timer is enabled; first scheduled run is September 26 at 02:00 Toronto.

## Authorized scope

The September 18 user decision implements the agreed refresh for the completed,
frozen Equibles roster: 2,222 mapped tickers. It uses existing EQUIBLES_API_KEY,
the two existing earnings-call endpoints, original raw-response retention,
company.sqlite and the established replay-safe publisher. No new provider,
schema, credential, ticker mapping or automatic model execution is introduced.

Run daily at 02:00 America/Toronto. Monday-Friday allocations are 80 requests
for recent reporters/downloads, 15 for delayed releases/retries and five for
fallback. Saturday and Sunday allocate all 100 to fallback, delayed releases
and unfinished downloads. Unused allocations can flow to other due work.
Complete partial downloads before new discovery. One shared UTC-day ceiling
counts all reservations, including failures, uncertain calls and pages.
Respect a smaller provider allowance. An optional operating_daily_cap in the
existing account checkpoint narrows every serial, parallel and daily caller;
absence preserves previous defaults. Earlier charges are never reset/refunded.

## Selection and time

Use the latest FMP earnings capture membership, matched by frozen instrument
identity, rather than assuming ticker text is stable. First-check dates are
the morning after reports in the past 14 days. Recheck unavailable calls on
days 3, 7 and 14, then weekly. Unresolved items survive the initial date window.

Use saved call dates to estimate reporting windows from the median of at least
two plausible historical intervals; estimates only affect queue priority.
Check those windows weekly, starting seven days before the estimate. Missing
or conservatively stale calendar evidence, or missing history, gets a rotating
30-day check. Oldest checked first prevents alphabetical starvation. Discovery
still requires Equibles' original fiscal labels and hasTranscript flag.
Sunday continues the queue rather than repeating Saturday's successful checks.

Incremental admission starts 14 days before activation. Older completed history
is not reopened. Existing saved fiscal calls are excluded. Current first-page
catalogues contain up to 100 earnings events; truncation is recorded. Complete
transcripts use 200-turn pages with the existing 50-page safety bound.
Source dates remain source metadata; availability remains actual local capture.

## Recovery and resource bounds

Persist each charge and request identity before dispatch. Retain each response
before processing it. Recovery processes retained responses without another GET;
uncertain interrupted requests are held. Recognized transient errors get at most
one retry on a later day, inside the same daily cap. Authentication, validation,
unknown errors and exhausted retries remain visible and held. Per-ticker
malformed/invalid responses retain their response identity and do not stop other
tickers. Unavailable events without fiscal labels stay in dated backoff; labels
are never inferred. Untrusted quota headers and authentication stop dispatch
until review. The 50-page limit is enforced before an excess request. A 429 ends work
until a later quota day. Missing transcripts remain pending with backoff.

Complete page bundles use the existing publisher. Publication failures retain
pages for a later zero-network retry. New captures enter a deduplicated private
Luna queue; this collector makes no model calls. Quota, pages, queue and daily
allocation survive restart. One job lock excludes concurrent Equibles work;
network calls occur outside canonical-store locks.

Each run is bounded to 100 shared requests, 400 MiB and 60 minutes of acquisition,
with a 30-second request timeout and one-second pacing. A 70-minute service
timeout also bounds initial database inventory. Persistent timer catch-up runs
one latest due slot, never all missed days. WSL must be running; no Windows wake
mechanism is added. Slot receipts prevent completed-slot repetition.

## Impact and required evidence

Changed component: Equibles incremental selection, private queue, optional
account quota narrowing, fixed timer and existing numeric status registration.
Shared schema, identity, date precision, physical locking and canonical
publication algorithms are unchanged.

Checks: tests.operations.test_equibles_refresh plus existing paid-policy, daily,
parallel, retry/continue, raw transcript publisher, fetch-run summary/history
and timer-binding regressions. All offline runs use isolated sources and explicit
temporary roots with network blocked. Validate systemd files, perform fresh
independent verification of stable source, then bounded live pre/post checks.
No full-suite trigger applies.

Operational authorization, exact activation and completion evidence are indexed
in CURRENT_OPERATING_ENVELOPE.md. Private evidence: .local/equibles-refresh-20260918/.


## September 18 validation and activation hold

There are 148 unique focused checks across the refresh, shared account policy,
existing callers, publisher and scheduler status integration. The final refresh
component run passed all 35 cases; the separate reviewer reran ten affected
cases and found no remaining blocker. Initial isolated-run errors came from
missing deployment fixtures, an unguarded multiprocessing test launcher and
the new telemetry entrypoint constant; all affected cases were corrected and
explicitly passed. Duplicate child output is excluded from coverage counts.
Systemd definition validation passed. No full-suite trigger applied.

The immutable inventory preview found 23 recent reporters and 176 fallback
candidates among the frozen 2,222 tickers. Baseline canonical transcript count:
45,290. These are selection and preparation evidence, not a live-fetch result.

Automatic approval review rejected the activation action before execution:
it requires explicit confirmation for the shared-cap state update, activation
state, unit installation and recurring timer enablement. A concrete approval
request was presented. No provider request or activation mutation occurred.

## September 25 activation

The user explicitly requested enabling the prepared refresh, supplying the
confirmation previously required for the shared-cap change and recurring
activation. At 2026-09-25T13:06:28Z (09:06 EDT), the new user service and timer
were linked and the timer enabled/started. Actual readback showed
active/waiting, with first execution September 26 at 02:00 America/Toronto
(06:00 UTC). No manual service start or provider request was performed.

The historical account was backed up byte-for-byte. Its sole change is
operating_daily_cap=100; all prior usage, completed histories, roster and
provider allowance evidence remain unchanged. New refresh state preserves the
existing 2,222-ticker roster. The first-slot admission boundary is September 25
at 02:00 Toronto, retaining the preceding 14-day incremental window. Activation
does not repeat the historical backfill or start automatic model processing.
The existing completed-backfill timer remains enabled and unchanged.

The established locked immutable inventory found 45,290 saved transcripts.
All eight reviewed core/collector-test/unit pins matched September 18 evidence.
Three status integration files had changed, so the applicable current focused
checks were run: 148 actual cases passed, followed by 10 status-summary cases.
The first command also included one nonexistent test-module name and therefore
exited with a loader error; its corrected module passed separately. This is
158 real passing cases, not a passing first invocation.

Systemd unit verification passed. A fresh independent reviewer found no
activation blocker in the pinned helper and preflight. Post-activation readback
verified the sole account change, unchanged usage and roster, an empty new
request ledger, and the enabled/waiting timer. Provider success and new
transcript publication remain unverified until an actual scheduled run.

Evidence: .local/equibles-refresh-activation-20260925/ contains the preflight,
original account bytes, current source pins, validation logs, independent
review, state activation receipt, timer readback and final postcheck.
