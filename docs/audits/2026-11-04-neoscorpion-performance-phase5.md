# NeoScorpion Phase 5 — production verification and rendering

Date: November 4, 2026, America/Chicago (measurements extend into November 5 UTC).
Starting, fetched main and confirmed LIVE: `0194726a1b6c79a34ffe9405ec0eb839fcdbfb90`,
Render `dep-dblt16c9v7es73butb40`. Main was re-fetched before release; unrelated
artwork/prototype files and existing jobs/databases were preserved.

## Outcome and measured bottlenecks

This package reduces Dispatcher template work and literal response bytes. It
does **not** claim fewer SQL statements, lower production CU-hours, or a measured
production cost reduction. Phases 1–4 and the rolling-learning audit were reviewed;
their delta protocol, context reuse and narrower event reads remain in place.

Ranked findings:

1. **Complete context plus render remains necessary for delta detection.** In the
   equivalent 25-mission fixture, the old unchanged delta returned 9,370 bytes
   after generating 509,479 bytes inside its fragment callers. Context assembly
   took about 30 ms in SQLite and 46 ms in loopback PostgreSQL; 63 SELECTs remained.
   No incomplete operational fingerprint is substituted for those dependencies.
2. **Repeated Jinja dictionary lookups and imports are measurable CPU work.** The
   initial cProfile trace recorded 10,778 Jinja attribute lookups in the delta and
   10,515 in critical completion. Dictionary dot access first attempts an attribute
   and catches an exception for ordinary row fields. Mission rows/DETAILS dominate
   template time. Critical rendering also imported context-bound status macros
   separately for every mission; those macros take explicit row parameters.
3. **Literal indentation repeats in every mission.** Removing 680 bytes of source
   indentation per mission saves 17,000 uncompressed bytes for 25 missions.
4. **Production background work is a separate resource consumer.** The hourly
   archive cycle at 01:00 UTC took 48.1 seconds; worker RSS rose from 174,055,424
   to 266,199,040 bytes while loading archive dependencies. The 01:10 cycle took
   1.627 seconds and added 114,688 bytes. Both reported `error=None`. This is not
   evidence of a leak, nor of a rendering-related memory improvement.

## Changes and safety

* Adapt finished Dispatcher row/truck dictionaries into request-local read-only
  dictionary views with native field attributes. This avoids exception-driven
  Jinja lookup for present fields, retaining mapping/JSON shape, false/zero/null
  values, missing-value behavior, dictionary method precedence, and the same ORM
  objects and permission values. Private keys cannot create trusted `__html__`
  behavior. There is no cache between requests, users, sessions or gateways.
* Import the canonical status/ACK macros once for critical rendering. Full-panel
  mission includes reuse the parent import; independent callers retain an import
  fallback. Every macro still receives the current explicit row and request helpers.
* Remove only static indentation before tags/template blocks in the mission-row
  source. No runtime HTML minification, textarea trimming, data removal or layout
  redesign. Saved note whitespace and HTML escaping have explicit regression tests.

Fresh authorization, current Sort resolution, canonical context assembly, clock
warnings, whole-fleet dependencies, signed SPEAR references and fragment hashing
remain authoritative. No mission is assumed independent of shared resources.
All candidate groups are still rendered to establish the existing fingerprints.
No polling interval, JavaScript protocol, business calculation, mutation route,
schema, index, background schedule, production configuration or SPEAR setting changes.

Fueler/Board/Driver/Hanzo adapter experiments did not demonstrate sufficiently
reliable benefit; their rendering paths retain their original implementation.
SPEAR fragment assembly also remains unchanged. No new early exit was released.

## Equivalent synthetic results

Fixtures contain one genuine ON/readings/OFF/Fueler-submitted visit and 24 planned
assignments. Providers are blocked. Before uses the exact starting fragment module
and mission template loaded from Git into isolated comparison processes, without
resetting the checkout. After uses final source. Stage medians use five requests;
the separate screen benchmark uses seven. SQL/context/SPEAR times overlap and must
not be added. Allocation measurement is a separate tracemalloc run.

### SQLite request stages (milliseconds)

| Request | Total before → after | Context | Jinja | SQL | JSON | Bytes before → after |
|---|---:|---:|---:|---:|---:|---:|
| Legacy full Dispatch | 46.42 → 44.00 | 28.91 → 28.65 | 13.36 → 10.54 | 2.10 → 1.82 | 1.14 → 1.14 | 543,978 → 526,978 |
| Unchanged mission delta | 52.19 → 49.36 | 29.97 → 29.60 | 19.31 → 16.11 | 1.77 → 1.79 | 0.05 → 0.06 | 9,370 → 9,370 |
| Critical, no fingerprints | 41.00 → 39.05 | 17.26 → 17.29 | 13.89 → 11.27 | 1.19 → 1.20 | 1.08 → 1.06 | 523,935 → 506,935 |
| Unchanged critical | 40.26 → 37.19 | 17.85 → 17.47 | 13.62 → 10.86 | 1.26 → 1.19 | 0.04 → 0.04 | 2,960 → 2,960 |

Jinja decreases approximately 17–21% in these SQLite stage measurements. Pre-context
processing, including request authorization and scope resolution, remains about
1.6–1.9 ms. The instrumented SPEAR builder takes about 0.21 ms in this simple
fixture, unchanged; that does not characterize a complex production optimizer run.
Overall context assembly remains the larger cost. Test-client request duration
includes response construction, not real network transmission to an operator.

### Loopback PostgreSQL request stages (milliseconds)

| Request | Total before → after | SQL | Context | Jinja |
|---|---:|---:|---:|---:|
| Legacy full Dispatch | 61.85 → 62.27 | 15.73 → 16.51 | 42.98 → 44.75 | 13.13 → 10.94 |
| Unchanged mission delta | 70.18 → 64.84 | 16.49 → 14.87 | 45.61 → 43.16 | 18.79 → 16.63 |
| Critical, no fingerprints | 52.00 → 51.77 | 11.08 → 11.78 | 26.60 → 27.80 | 13.96 → 11.60 |
| Unchanged critical | 50.16 → 53.06 | 11.09 → 13.21 | 26.85 → 30.23 | 13.37 → 11.40 |

PostgreSQL rendering improves approximately 12–17%; total latency is not uniformly
lower because query/context variation can exceed the saved rendering time. No
claim of universal end-to-end latency improvement or reduced Neon SQL time is made.
Every measured read retained one connection checkout/transaction and zero writes.

Changed-mission measurements preserve Phase 3 behavior:

| Response | Before bytes | After bytes | SELECTs before/after |
|---|---:|---:|---:|
| One changed Dispatcher mission | 34,864 | 34,184 | 63 / 63 |
| One changed critical mission | 28,389 | 27,709 | 53 / 53 |
| Next unchanged delta | 9,370 | 9,370 | 63 / 63 |

Only that mission is sent; the other 27 transport groups are reused. Request
frequency remains unchanged. These are actual uncompressed JSON body lengths,
not guessed compression or production bandwidth savings.

Other screen SELECT counts before/after remain: SPEAR 48, Fueler 36, Board 35,
Driver unclaimed 18/claimed 47, Hanzo live 29/document 35. Unchanged bodies remain
SPEAR 2,891, Fueler 175, Board/Driver/Hanzo 151 bytes. Full SPEAR bodies remain
28,798 bytes. Timings for these unchanged implementations are retained in the
evidence register; small timing differences are not attributed to this change.

Memory tradeoff: SQLite peak traced allocation is 3,295,640 → 3,614,481 bytes for
full Dispatch; 1,464,280 → 1,763,465 for seeded delta; 4,908,337 → 4,705,383 for
unseeded critical; 1,161,569 → 1,397,120 for seeded critical. Attribute copies cost
roughly 0.3 MB per 25-mission request; they expire with the request. There is no
claim of lower process RSS or a sustained production memory reduction.

## Read-only production observations

Render metrics cover November 3 00:00 through November 5 01:00 UTC. Provider-`mb`
bandwidth sums are 3,795.48246 for November 3, 3,574.28008 for November 4 and
5.67108 for the available November 5 00:00 bucket. The peak hour remains
853.5678 at November 4 07:00 UTC. CPU samples peak at 0.15071337 provider CPU
units and memory at 364,171,260 bytes across different instances/releases.
These mixed historical samples are not matched before/after experiments.

A complete six-page access-log sample, November 4 06:00–07:00 UTC, contains:

| Endpoint | Requests | Logged response bytes | Largest response |
|---|---:|---:|---:|
| Dispatch full panel | 202 | 127,888,127 | 673,037 |
| Critical completion | 202 | 97,779,706 | 566,521 |
| Fueler panel | 106 | 1,594,766 | 37,512 |
| Board panel | 56 | 853,004 | 19,048 |

All 566 sampled responses were HTTP 200. Dispatch paths account for about 99% of
these logged panel bytes, supporting their priority. Gunicorn logged body bytes
are not equivalent to Render's billed network bytes. This sample predates Phase 3;
it establishes the historic contributor, not current residual waste.

The post-Phase-4 window 00:42:44–01:03 UTC contains zero matching Dispatch
live-panel log entries. Path-specific Render HTTP metrics returned empty arrays.
Authenticated concurrent-session counts, equivalent mission populations and a
comparable busy post-release Sort are unavailable. No normalized production
bandwidth, CPU, request-rate or cost saving is asserted. Historical HTTP 429s and
the earlier MotherBrain Google-poll 500 remain outside this rendering package.

At 01:03:35 UTC, a bounded read-only PostgreSQL activity snapshot found two other
idle connections and no idle-in-transaction session. At 01:09:26, Neon's control
plane reported the endpoint **idle**, suspended at 01:09:01, with its existing
300-second suspension setting. Actual suspension is therefore observed under
quiet conditions. The subsequent 01:10 archive cycle remained scheduled.
This observation does not measure hourly compute consumption or prove that every
quiet interval suspends. No persistent diagnostic connection was installed.
CU-hour/transfer metrics and per-query production statistics remain unavailable;
`pg_stat_statements` is absent from the previously checked installation. No
extension, statistics reset, configuration or index change was performed.

## Focused verification

* **115 SQLite/unit cases passed**, plus 301 passing subtests. Coverage includes
  canonical deltas, permission/gateway/Sort isolation, lifecycle/submission,
  current projections, truck-driver history, CSRF, CSP/HSTS, rolling learning and
  replanning. No skipped/expected-failure cases; existing warnings remain visible.
* **22 unique PostgreSQL cases passed**, including three new rendering parity,
  macro-import and note-preservation cases, plus independent simultaneous readers,
  immutable event/inventory evidence and Phase 4 projections. The final three
  rendering cases were rerun after narrowing the adapter scope. Benchmark schemas
  were separate synthetic UUID schemas; existing database directories were retained.
* **30 JavaScript tests passed**; no application JavaScript changed.
* **Three Chromium workflows passed**: simultaneous Dispatcher sessions/Fueler
  submission, newly eligible COMPLETE, exactly one event and one deduction (7,000
  gallons remaining), changed/added/removed/reordered missions, permission loss,
  open dialog during transit, stale/missing-marker fallback, dirty notes/focus,
  desktop/390px mobile, SPEAR-only refresh and unchanged Hanzo DOM preservation.
  No JavaScript errors; unchanged state triggered zero unnecessary panel requests.
  COMPLETE appeared after an explicit poll in 133.61 ms (prior Phase 4 receipt:
  140.41 ms). Another focused critical workflow measured 66.53 ms. Polling wait is
  excluded; these single-run browser timings do not establish a production gain.
* Python compilation, live-update JavaScript syntax and `git diff --check` passed.

The new test's initial note parser assertion omitted the template's deliberate
newline immediately after `<textarea>`; HTML consumes that single newline before
displaying the saved value. The assertion was corrected to verify the exact raw
content and escaping. No application behavior or pre-existing test was weakened.
The massive application/audit suite and WebKit were not run in this focused phase.

Machine measurements and case evidence:
`2026-11-04-neoscorpion-phase5-evidence.json`.
Local scripts/logs/screenshots: `instance/scorpion-phase5-20261104/` (ignored).
The first browser invocation used the older tests' default ignored artifact paths;
the final rerun redirects collected modules to the Phase 5 evidence folders.

## Release, preservation and remaining work

Exact changed files: this report, its JSON evidence register,
`app/services/neoscorpion_dispatch_fragments.py`,
`app/neonodes/neoscorpion/routes.py`,
`app/templates/neonodes/neoscorpion/_dispatch_mission_rows.html`,
`tests/test_neoscorpion_rendering.py`, and
`tests/test_neoscorpion_rendering_postgres.py`.

Release uses main/Render AutoDeploy. The commit containing this report is the
release candidate; exact resulting SHA/deployment, startup, health/login,
CSP/HSTS/CSRF, published asset hashes and authenticated read-only NeoScorpion
pages are checked after deployment and recorded in the completion receipt and
final response. Live operational mutations are not used for verification.

All production missions, physical evidence, inventory, historical records,
archives, settings and background work remain untouched. SPEAR recommendations
ON, automation OFF, calibration OBSERVE, optimizer SHADOW, rolling 30 calendar
days/seven-day half-life and Sort-end cleanup are preserved. Twelve unrelated
untracked-file hashes match the saved preservation inventory. No jobs, histories,
worktrees or existing synthetic database directories were cleaned up.

Next opportunities require evidence: collect a comparable busy Sort with full/
delta/critical/SPEAR frequencies and deployment/client versions; obtain read-only
Neon consumption statistics; profile representative complex planning; and design
a complete dependency model before attempting a render/context early exit. The
remaining 63/53 query contexts and large first unseeded critical response are
explicit limitations. This package delivers verified rendering/byte improvements,
not a claim that all NeoScorpion performance work is finished.
