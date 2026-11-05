# NeoScorpion performance — Phase 4

Local audit date: November 4, 2026 (America/Chicago). Production measurements
extend into November 5 UTC. Starting main/origin/main and LIVE source:
`dd072dc67cbe426177e89140a2850d307a0afbad`, Render
`dep-dbls266q1p3s738u289g`. No concurrent main changes were present at preparation.

## Outcome and scope

This release removes confirmed duplicate reads and unnecessary unclaimed-Driver
context construction. Phase 3's mission-delta protocol, templates, JavaScript,
polling cadence, permission checks and SPEAR policies are unchanged. There is no
global data/HTML cache, new schema, worker, configuration change or history purge.

The final release SHA/deployment, startup and read-only production receipts are
recorded in the completion message and `instance/scorpion-phase4-20261104/`.
The report is part of that release; it cannot embed its own future commit hash.

## Equivalent 25-mission measurements

The fixture contains one genuine ON/readings/OFF/Fueler-submitted visit and 24
planned assignments. A canonical saved Actual change exercises the changed
mission response. External provider connections are blocked. Measurements are
uncompressed JSON body bytes, including escaping, not estimated wire compression.
Warm panels use seven samples; deltas and detailed profiles use five. Timing is
local SQLite, not Neon network latency or measured production CU savings.

| View | SELECTs before | SELECTs after | Response bytes before → after |
|---|---:|---:|---:|
| Dispatcher | 73 | 63 | Changed mission: 34,864 → 34,864 |
| Critical completion | 62 | 53 | Changed mission: 28,389 → 28,389 |
| SPEAR unchanged | 57 | 48 | 2,891 → 2,891 |
| Fueler unchanged | 38 | 36 | 175 → 175 |
| Board unchanged | 37 | 35 | 151 → 151 |
| Driver, **no truck claimed** | 43 | 18 | 151 → 151 |
| Hanzo unchanged | 31 | 29 | 151 → 151 |
| Driver, **truck claimed**, separate equivalent run | 49 | 47 | Full panel: 92,692 → 92,692 |

Dispatch removes 13.7% of SELECT round trips; critical removes 14.5%; SPEAR
removes 15.8%; unclaimed Driver removes 58.1%. The Driver shortcut does **not**
apply to claimed work. Its warm unchanged request fell from 18.80 to 5.15 ms;
the separate claimed run remained approximately 19.7 ms.

All measured GET workloads wrote zero rows. With an independent session per
profile request, both versions acquired one connection and one transaction.
This release reduces SQL inside each request, not the request/transaction count.

| Instrumented request | Before | After |
|---|---:|---:|
| Full Dispatch SQL execution | 2.02 ms | 1.75 ms |
| Full Dispatch context assembly | 31.28 ms | 28.93 ms |
| Full Dispatch Jinja rendering | 12.97 ms | 13.14 ms |
| Full Dispatch total | 48.53 ms | 46.25 ms |
| Seeded critical context assembly | 19.76 ms | 17.58 ms |
| Seeded critical total | 42.51 ms | 39.99 ms |
| Forced identical mission-delta context | 31.96 ms | 30.70 ms |
| Forced identical mission-delta Jinja | 18.93 ms | 19.71 ms |

These small timing differences are not proof of sustained CPU savings. Canonical
HTML rendering remains: the forced identical delta internally builds 509,479
fragment bytes in both versions. Its returned body remains 9,370 bytes. Normal
unchanged revision polling still makes zero panel requests.

Separate-run traced Python peak allocations: full Dispatch 3,296,752 → 3,272,026
bytes; seeded critical 1,177,175 → 1,143,294; forced identical delta 1,399,699 →
1,618,989. The latter increased; no memory reduction is claimed for that path.
These are transient Python allocations, not sustained Render RSS.

The before profile ran pinned Phase 3 service/presentation functions in a
separate guarded process. It did not reset or overwrite the working checkout.
Raw baseline/profile/delta and query call stacks remain in the evidence folder;
the checked-in evidence JSON records the measured values.

## Confirmed bottlenecks and exact corrections

1. **Work and tank evidence was loaded twice.** The canonical manual builder now
   passes its existing WorkIndex into the DETAILS/continuation assembler.
   Cancelled/non-displayed owners are filtered by permanent assignment ID.
   Visit grouping is built once instead of rescanning every visit for every row.
2. **Three plan reads overlapped.** A single current-Sort plan read supplies
   outstanding plans, promoted continuation dependencies and assignment receipts.
   Existing status/identity filters and independent-caller fallbacks remain.
3. **Truck rows/history were queried again.** Rendering and resource timing reuse
   the already-loaded canonical nightly selections and their truck histories.
   Fueler/Board/Hanzo timing receives the same read inputs explicitly. Historical
   cross-Sort physical resource visits are still independently loaded.
4. **Receipt names repeated known reads.** Active employee names and gateway
   trucks come from the context already read; missing historical/inactive names
   retain a bounded batch lookup. Attribution does not depend on active status.
5. **Current-Sort events were read three times.** One compact projection supplies
   totals, truck-release evidence and assignment receipts. It selects event
   identities, physical gallons/release and only five receipt snapshot facts:
   `fueler_assigned_at`, `truck_assigned_at`, `fueler_id`, `truck_id`,
   `assignment_actor_id`. Unrelated forecast arrays and full event ORM rows are
   not loaded for these receipts. JSON array projection preserves strings,
   booleans, numbers, objects, arrays and null without converting them into IDs;
   this is explicitly tested on PostgreSQL and SQLite.
6. **Arrival rows were loaded twice.** One same-operation arrival set supplies
   the existing display selector and chronology-aware presence service. Cancelled
   arrivals, repeated-tail chronology, HERE evidence and original selection order
   retain their existing independent rules.
7. **Unclaimed Driver built then discarded fleet work.** The established `-1`
   truck sentinel can match no physical truck. It now returns the same empty
   rows plus fresh operation, settings, nickname, refresh setting and revision.
   The driver roster and authorization are still read. Claiming a truck through
   the canonical API immediately restores the full work path.

All reuse is explicit within one canonical request/context. Mutation services
continue reading fresh state under their existing locks. No persistence, TTL,
cross-user HTML reuse or authorization caching was added. Source fallback paths
remain available when callers do not supply shared read inputs.

## Why other shortcuts were withheld

A mission's Actual/ON/OFF/submission can affect another assignment's truck,
Fueler, inventory reservation, travel and SPEAR calendar. Full fleet planning and
summaries are therefore retained. No single-mission independence was assumed.
Learning-window observations and current-Sort PROF observations have different
eligibility and limits; they remain separate queries. Seven-day/open cross-Sort
resource history is not replaced by a current-Sort-only WorkIndex.

No general revision-only early exit was deployed for Dispatch, Fueler, Board or
Hanzo. A complete cheap fingerprint covering clock warnings, settings, resource
state, current authority and calibrated planning is still unproven. Rendered
fingerprints remain the correctness boundary. The unclaimed Driver case is an
empty-result proof, not a claim that operational fleet state is unchanged.

## Production Neon/Render investigation — read-only

No extension was enabled, statistics reset, index altered, ANALYZE executed or
hosting setting changed. `pg_stat_statements` is not installed, so current
production SELECT frequency/per-query durations cannot be recovered from it.

The database snapshot reported 3 backends, 1,878,178 commits, 1,130,706 rollbacks,
2,134,805,118 tuples returned, 1,001,732 blocks read and 5,382,144 temporary bytes;
`stats_reset` was null. These cumulative counters have no controlled traffic
window and are **not** query counts or attributable Phase 3 savings. A later
sample saw two idle connections, oldest idle 336 seconds. Their presence does
not establish active SQL, session ownership or the cause of compute activity.

The existing Neon endpoint reports 300-second suspend timeout and 0.25–2 CU
limits. No setting changed. Inspection itself can wake compute. Integrated CU
hours, active-time and database-transfer series were unavailable in the tools.

Cumulative table counters include 64,212 tank-state sequential scans / 123,587,186
rows read, and 59,309 Work sequential scans / 29,131,408 rows read. These span
earlier releases; they do not prove a current missing index. A read-only EXPLAIN
of current-operation assignment/Work lookup uses the assignment operation index
and `uq_scorpion_work_assignment_visit`. Existing event/plan operation indexes
were confirmed. No new index was justified or added. Production has 666 events;
average stored snapshot size is 1,564 bytes, maximum 1,877 in the aggregate sample.
Actual Neon byte savings from the narrower projection remain unmeasured.

Observed archive cycles: 23:36:36, 23:50:01, 00:00:42, 00:10:01, 00:20:01 UTC;
all reported `error=None`. The hourly cycle took 42.802 seconds, others about
1.3–1.9 seconds. Required FROST/archive work remains scheduled; no new minute
polling or changes to Phase 2's deadline scheduling/Sort-end cleanup were made.

Render's hourly provider-`mb` bandwidth series includes 845.87744 at November 3
07:00 UTC and 853.5678 at November 4 07:00, with 190/189 HTTP 429 counts in the
corresponding returned request-count buckets. Busy HTTP 200 buckets reach about
10,800/hour. Historical CPU samples reach approximately 0.15 provider CPU units;
memory samples reach about 364 MB across different instances/deployments.

Phase 3 became LIVE at 23:36:28 UTC on November 4. The 23:00 bucket is mixed;
the 00:00 bucket is partial. Their reported bandwidth (8.79723 / 2.5225906) and
quiet request counts cannot be compared with those busy operational periods.
The sampled post-Phase-3 interval through 00:26 contains **no Dispatch live-panel
requests**. No comparable busy Sort/session/mission population is available yet;
no production bandwidth, CPU or Neon savings percentage is claimed.

One historical 500 in the returned metrics was attributed by read-only logs to
`POST /motherbrain/google-live-poll/execute` at 05:21:14 UTC, before Phase 3.
It is outside this package and remains an independent follow-up, not silently
fixed or attributed to NeoScorpion. No production test failures were induced.

## Focused verification and preservation

The final suites cover canonical completion, exactly-once inventory, immutable
physical visits, receipts/plans, cross-mission resource timing, arrival chronology,
Sort transitions, stale saves, handoff/submission races, revocation, CSRF and
CSP/HSTS. New projection tests also preserve malformed legacy JSON types rather
than promoting them into numeric attribution, including SQL NULL snapshots.
No tests were removed, marked expected-failure or weakened; no cases were skipped
in the recorded runs. Existing warnings remain visible.

Across the recorded runs, 185 unique Python case IDs passed (162 SQLite/unit,
23 disposable PostgreSQL), plus 30 JavaScript tests and three Chromium workflows.
Repeated cases are counted once. Final run summaries: context regression 61;
learning/resource/projection 57; authority/resource/security 50; PostgreSQL 21;
final legacy projection 16. That final projection run includes SQL NULL cases
on both engines; the machine inventory distinguishes overlap. The 185-ID collection reconciles
executed coverage; it is not itself a replacement for execution.

Three essential Chromium workflows passed: simultaneous Dispatcher/Fueler
handoff, dirty notes/focus, open-dialog-in-transit, permission loss, changed/added/
removed/reordered missions, original DOM-node reuse, stale/fallback safety,
desktop and 390px mobile, SPEAR-only updates and unchanged Hanzo document.
Newly eligible COMPLETE appeared 140.41 ms after an explicit poll in this run
(configured polling wait excluded). One physical event/one 3,000-gallon deduction
left 7,000 gallons. This is synthetic responsiveness evidence, not production
end-to-end timing. Browser receipts/screenshots are retained in the evidence
folder; no production fueling actions were performed.

Exact test-run totals and measured JSON are in
`2026-11-04-neoscorpion-phase4-evidence.json`. The massive audit suite was not run.
Compilation, JavaScript checks and `git diff --check` are required before push.

Exact changed files:

* `app/services/neoscorpion.py`
* `app/services/neoscorpion_assignment_times.py`
* `app/services/neoscorpion_pending_uplift.py`
* `app/services/neoscorpion_resource_timing.py`
* `app/neonodes/neoscorpion/routes.py`
* `tests/test_neoscorpion_phase4.py`
* `tests/test_neoscorpion_phase4_postgres.py`
* This audit and `docs/audits/2026-11-04-neoscorpion-phase4-evidence.json`

Historical/production records, archives, credentials, unrelated artwork and
prototypes, existing jobs and synthetic database directories are preserved.
SPEAR recommendations remain ON, automation OFF, calibration OBSERVE, optimizer
SHADOW, rolling 30 calendar days/seven-day half-life unchanged. There is no
historical repair, production test mutation, schema migration or cleanup.

Release uses main and Render AutoDeploy. Exact SHA LIVE, bootstrap/worker logs,
health/login, security headers/CSRF, unchanged published Phase 3 assets and
authorized read-only NeoScorpion pages are verified after deployment and recorded
in the completion receipt. Remaining work is a complete safe render-dependency
model and representative busy production comparison, not an unverified cache.

Current deployment/health behavior was checked against Render's primary
[deployment](https://render.com/docs/deploys) and
[health-check](https://render.com/docs/health-checks) references; Markdown retrieval
was unsupported by the reader, so the HTML references were read.
