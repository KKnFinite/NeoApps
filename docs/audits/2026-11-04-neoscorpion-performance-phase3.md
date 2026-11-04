# NeoScorpion performance — Phase 3

Date: 2026-11-04. Scope: bandwidth first; preserve fueling authority, evidence,
SPEAR and operational responsiveness. No production test mutations.

## Starting point and independent measurements

Audited starting main/origin/main and Render LIVE source:
`5910faed80cd7899943799b3976cd9e1a6857be3`, deployment
`dep-dblr6qdg1s2s73c5mr7g`. Phase 1 and Phase 2 reports and implementation were
reviewed before editing. Unrelated untracked work and existing test jobs were
preserved. The final release SHA/deployment and HTTP/asset receipts are recorded
in the release completion message and `instance/scorpion-phase3-20261104/`.

The local baseline uses 25 synthetic current-Sort departures, a canonical physical
visit, mocked/disabled external transports and SQLite. After measurements use
the same population and a canonical saved Actual change on one mission. Bytes
are actual uncompressed response-body bytes, including JSON escaping; they are
not estimated gzip or production traffic savings. Warm medians use five delta
requests and seven legacy-panel requests. Millisecond comparisons are local,
subject to run noise and different instrumentation; PostgreSQL concurrency is
validated separately, not represented as a production latency benchmark.

| Workload | Starting bytes | Final bytes | SELECTs before → after | Local median ms before → after |
|---|---:|---:|---:|---:|
| Clean Dispatch refresh after one mission changes | 543,001 | 34,864 | 77 → 73 | 49.82 → 57.59 |
| Critical completion after one mission changes | 523,201 | 28,389 | 66 → 62 | 39.92 → 42.38 |
| Identical Dispatch snapshot, if explicitly requested | 543,001 | 9,370 | 77 → 73 | 49.82 → 56.61 |
| First unchanged critical check using initial-page fingerprints | 523,201 | 2,960 | 66 → 62 | 39.92 → 43.42 |
| SPEAR unchanged | 2,875 | 2,891 | 57 → 57 | 32.37 → 31.77 |
| Fueler unchanged | 175 | 175 | 38 → 38 | 24.20 → 24.35 |
| Board unchanged | 151 | 151 | 37 → 37 | See evidence JSON |
| Driver unchanged | 151 | 151 | 43 → 43 | See evidence JSON |
| Hanzo unchanged | 151 | 151 | 31 → 31 | See evidence JSON |

Dispatch one-mission response bytes decrease 93.58%; critical response bytes
decrease 94.57%. A forced identical snapshot decreases 98.27%. Normal unchanged
revision polling still requests **zero panels**; those forced requests are
diagnostic workloads, not additional scheduled traffic. SPEAR's additional 16
bytes carry fresh edit authority. No refresh interval was increased.

## Confirmed causes and changes

1. A clean operational revision fetched the entire Dispatch HTML, although most
   mission groups were unchanged. The existing canonical panel is now divided
   into comment-delimited mission groups, truck cards, assets and summary. Each
   mission group includes its row, physical history, truck lines and DETAILS.
   The same templates render initial pages, complete fallback and delta replies.
   No alternate operational state model, HTML cache or mutation system exists.
2. The first critical check had no fingerprints for the page already displayed,
   so it resent every mission. Initial full rendering now seeds both full and
   critical fingerprints using the **same canonical pre-SPEAR read**. The manual
   container snapshot retains loaded immutable/ORM evidence and does not repeat
   the database read or optional planner.
3. Dispatch physical cards, driver management and eligible-choice presentation
   repeatedly loaded the same truck/driver roster. A GET/HEAD-only request-local
   reuse removes four SELECTs in this fixture. POST operations remain fresh;
   commit/rollback invalidates the established request cache. No user data,
   permissions or HTML survives across requests.
4. A Fueler Data dialog opened while a full response was in flight could evade
   the request-start dialog guard. The apply-time guard now preserves that
   dialog, using the independent existing critical completion path instead.
5. Revision sharing previously did not carry Dispatch edit authority. Fresh
   revision/SPEAR/panel responses now carry `can_edit`. Permission loss disables
   stale mutation controls while retaining operator drafts. Legacy peer hints
   lacking the new field cannot suppress a fresh authorized revision read.

## Transport, draft, identity and failure safety

Fingerprints bind HTML to the opaque authenticated session/gateway scope,
operation, current view/edit/settings permissions and fragment identity. Each
request performs existing fresh authorization. CSRF renewal and renewed signed
SPEAR decision references are normalized **only for hashing**; actual tokens are
never normalized or broadcast. Fresh decision references travel explicitly and
replace hidden references, preserving actor/plan/evidence validation.

Only unchanged chunks reference the browser's previously applied DOM. The
external helper validates a detached candidate before moving original live
nodes. Missing/malformed markers cause a complete authorized fallback; they
cannot create or replay a POST. Metadata is bounded to 80 entries/6,500 bytes per
parsed map; larger mission populations still receive full operational coverage.

A complete mission/rank manifest handles additions, deletions and ordering.
Ranks are separate from markup digests so moving one flight need not resend all
other rows. Clean removed groups disappear; dirty removed groups retain drafts
with mutation controls disabled and a state-change warning. New mission groups
use canonical markup and existing initialization. Tail/mission/Sort identity is
never reconstructed from displayed text.

Existing generation/revision checks reject stale responses. The full path checks
drafts, personal saves and open dialogs before requesting and again before
applying. Dirty screens retain the independent critical patcher, including newly
eligible COMPLETE controls. Focus/selection, actual unchanged DOM nodes, scroll,
DETAILS, search, filters and row-height preferences are preserved. No automatic
POST retry, shared private HTML, permanent storage, new polling or background
connection was introduced. A wrong explicit operation is rejected before the
expensive planner/context path.

## CPU, rendering, memory and database limits

The wire optimization still renders canonical rows to prove equality. It does
**not** claim to avoid all server rendering or context assembly. In an additional
25-mission profile with a fresh session per request:

| Metric | Legacy full response on final code | Identical delta on final code |
|---|---:|---:|
| Context assembly | 32.42 ms | 32.21 ms |
| SQL execution | 1.95 ms | 2.05 ms |
| Template rendering | 12.85 ms | 18.57 ms |
| Total request | 49.51 ms | 54.13 ms |
| Connections / transactions | 1 / 1 | 1 / 1 |
| Writes | 0 | 0 |
| Separate-run traced Python peak allocation | 3,297,576 bytes | 1,405,149 bytes |

The delta still constructs 509,479 bytes of canonical fragment HTML internally.
Lower transient allocations do not establish lower sustained Render RSS. An
unchanged seeded critical response peaks at 1,175,705 traced bytes versus
4,929,796 for the unseeded critical response in the separate run. These are
Python allocations, not container memory measurements. Initial rendering also
renders manual critical representations to seed the page; this is an intentional
one-page-load cost, with no second canonical SQL read.

Fueler/Board/Driver/Hanzo safe digest responses remain unchanged. Their complete
dependency closure includes settings, authorization, physical/resource state,
weather/timing inputs and clock warnings. A revision-only early exit was not
released without that proof. SPEAR's existing bounded SHADOW reuse remains; this
release does not add an unsafe shared baseline-plan cache or alter predictions.

## SPEAR, background operations and production observations

Read-only production settings confirmed recommendations `true`, automation
`false`, calibration `observe`, whole-sort `shadow`. Rolling 30 calendar days,
seven-day half-life, sample eligibility/outliers, signed action references and
Dispatcher authority are unchanged. SPEAR-only responses remain independent of
operational panel rendering. No new reconciliation/heartbeat or timer exists.

Phase 2 cleanup and request-local safeguards remain in place. Active browser
revision requests legitimately access PostgreSQL. Existing ten-minute idle
background cycles were observed at 22:50, 23:10 and 23:20 UTC; no new minute-level
poll was added. Required FROST capture at 23:00 took 43.208 seconds and increased
RSS approximately 90.9 MB. That is real scheduled work, not an idle task to
disable. The sampled 22:40–23:25 logs contained 33 entries, no detected 5xx,
traceback, OOM or worker-timeout evidence, and no truncated page.

Render's available 22:00–23:00 buckets report bandwidth 17.800728 and 0.0024032593
provider `mb`, memory samples 194,535,420 and 185,831,420 bytes on different
instances, and no CPU series. Request-count buckets contain 200/302/304/404,
with no returned 429/5xx series. Different sampling boundaries, deployments and
quiet traffic prevent attribution to this release or comparison with the busy
November 3 sample. Sustained Neon CU, transfer and active-time savings are
**unmeasured**. Audit queries themselves can wake Neon.

## Focused verification

All mutations used synthetic SQLite/disposable loopback PostgreSQL records;
external provider sockets were blocked by the established test guard. No massive
full-system suite was run. Existing warnings remain visible.

Recorded final runs (passing subtests counted separately):

* Canonical fragment/Phase 1 bandwidth modules: 20 passed, 4 passing subtests.
* Final delta SQLite + delta PostgreSQL + Phase 2 PostgreSQL: 27 passed,
  11 passing subtests (10 SQLite, 11 delta PostgreSQL, 6 Phase 2 PostgreSQL).
* Phase 2, rolling learning, optimizer, Fueler submission, live authority and CSRF:
  72 passed, 19 passing subtests.
* Security headers and SPEAR replanning: 20 passed, 271 passing subtests.
* Driver/vendor roster and SPEAR reconciliation (SQLite/PostgreSQL): 88 passed,
  5 passing subtests. Includes preserved Sort-end purge, crash/locking safety,
  historical outcomes and finalized-observation no-write contracts.
* JavaScript: 27 passed, including 1/2/5-tab coordination, scope isolation,
  Retry-After, leader failure, stale hints and permission propagation.
* Essential Chromium: two new workflows plus the existing bandwidth workflow
  passed. The two-session handoff exposed COMPLETE in 136.63 ms after an explicit
  poll, excluding the unchanged configured polling interval. One event and one
  3,000-gallon deduction left 7,000 gallons. An unrelated mission's original DOM
  node and dirty text/focus survived. New/deleted/reordered missions, 390px
  overflow, dialog-in-transit, atomic fallback and permission-loss drafts passed.

The new PostgreSQL reader fixture initially held login work across a synthetic
outer app context and broke its barrier. Authentication now completes before
racing independent read transactions. An initial reorder fixture moved an
already-first mission earlier; it now crosses an actual peer. Browser fixtures
now advance beyond the existing revision-sharing TTL and publish the upstream
revision for synthetic mission edits. These were new test-harness corrections,
not relaxed operational guards or changes to existing tests.

Screenshot review exposed a new transport integration defect before release:
personal sorting moves actual rows independently of comment boundaries. Fragment
reuse now binds the marker to the current physical row group by mission ID.
The browser harness explicitly records any manifest mismatch, verifies all
original mission IDs survive addition/removal, and checks no refresh warning is
left visible. The final three-workflow browser run passed after this correction.

Screenshots and browser receipts remain in
`instance/scorpion-phase3-20261104/browser/`. Measurements and sanitized receipts
are summarized in `2026-11-04-neoscorpion-phase3-evidence.json`. Python compilation,
JavaScript syntax and `git diff --check` are required before the release.

## Scope, preservation and release

Exact changed files:

* `app/neonodes/neoscorpion/routes.py`
* `app/neonodes/neoscorpion/vendor_routes.py`
* `app/services/neoscorpion.py`
* `app/services/neoscorpion_dispatch_fragments.py`
* `app/services/neoscorpion_drivers.py`
* `app/static/js/live_revision.js`
* `app/static/js/neoscorpion_dispatch_delta.js`
* `app/static/js/neoscorpion_fuel_dispatch_live.js`
* `app/templates/base.html`
* `app/templates/neonodes/neoscorpion/_fuel_dispatch_panel.html`
* `app/templates/neonodes/neoscorpion/fuel_dispatch.html`
* `tests/browser/test_neoscorpion_dispatch_delta.py`
* `tests/js/live_revision.test.js`
* `tests/test_neoscorpion_dispatch_delta.py`
* `tests/test_neoscorpion_dispatch_delta_postgres.py`
* `docs/audits/2026-11-04-neoscorpion-performance-phase3.md`
* `docs/audits/2026-11-04-neoscorpion-phase3-evidence.json`

Changes are confined to canonical Dispatch rendering/transport, request-local
driver roster reuse, fresh revision authority, focused tests and this evidence.
There are no schema, lifecycle, inventory, learning-format, permission-rule,
refresh-cadence, credentials or hosting changes. Existing jobs, histories,
untracked artwork/prototypes, synthetic databases, production operational records
and R2 archives are preserved. No historical backfill or data repair was run.

Release is main → Render AutoDeploy only. Exact SHA LIVE, startup/worker logs,
health/login, CSP/HSTS/CSRF, published JavaScript and authorized NeoScorpion
read-only pages must pass post-release checks. A quiet/no-current-Sort production
page load establishes availability, not a production fueling simulation. Active
workflows are proven only with synthetic fixtures. Remaining optimization work
is canonical context/query reduction and a proven complete early-exit dependency
model; no production savings or eliminated rendering is claimed here.
