# NeoSektor and NeoErmac production performance

November 4, 2026, America/Chicago; execution and rollout extend into November 5 UTC.
Starting/fetched main and confirmed LIVE: `8fd1cd8d7701a11883ee6c820f38bd0b80a5dc5f`,
Render `dep-dbltl1avcj2c73fs2i60`. The November 4 NeoScorpion Phases 1–5 and rolling
learning audits were reviewed. Existing worktrees, jobs and unrelated source work
were inspected and preserved. This is a focused two-application investigation,
not a full-system regression audit.

## Outcome and ranked causes

1. **Driver Routing's fast hints were duplicated across equivalent tabs.** Its
   existing 1.5-second invalidation watcher runs in addition to the configured
   five-second canonical safety poll. These serve different dependencies; neither
   is removed or slowed. The watcher previously bypassed the existing scoped
   revision coordinator and ignored its Retry-After deadline. Real-browser
   verification now demonstrates fewer duplicate hints while retaining both safety polls.
2. **Unused provider history travelled with current operation ORM rows.** A bounded
   read-only sample of the latest 30 production operations measured average logical
   snapshot text of 15,692 bytes, maximum 22,645. On-disk `pg_column_size` averaged
   2,249 bytes (maximum 3,030); compressed storage is not wire transfer. None of the
   Sektor/Ermac screen builders uses that history. Door View can resolve operations
   twice through distinct rules, multiplying this unnecessary projection.
3. **Sektor revision SQL repeated the same unique Sort lookup.** Every child branch
   repeated gateway/date/sort resolution. A statement-local CTE plus scalar ID now
   resolves it once while preserving all existing aggregates and timer phases.
4. **Lifecycle selection lazily loaded parent and child settings separately.**
   Driver/Conductor and the four Ermac state reads now fetch the same relationship
   in one query. Counts/EBM/WBM already had this optimization and retain it.
5. **Large initial HTML remains, especially Building Lineup choices.** Current live
   protocols already omit unchanged state before Jinja rendering, reuse Lineup
   common data and provide Outbound row deltas. Replacing those with another
   rendering/cache system would add risk without demonstrated benefit.

## Changes by application and safety

NeoSektor reuses `NeoLiveRevision.revision` for opaque Driver version hints. The
successful version endpoint adds the same HMAC session/user/gateway partition as
the page; errors never publish reusable hints. Before that header was added, the
real-browser test correctly found no sharing, despite passing mocked transport
tests. The final browser test proves the complete path. Broadcasts carry primitive
fingerprints only, never routing state, personnel, aircraft, HTML or mutations.
Changed hints still cause each screen to fetch its own authorized canonical state.
Opaque versions use request time ordering, not numeric/string comparison. Unsupported
coordination and missing/mismatched scope retain independent/fail-closed behavior.
Existing locks, bounded fallback, hidden-tab handling, resume and 429 cooldown are reused.

The two versioned asset URLs were advanced so new pages receive the updated
coordinator/watcher. No layout or operational control was redesigned.

Both applications opt into deferring only `flight_api_last_poll_snapshot_json` on
an exact GET/HEAD screen allowlist. Identity, status, Sort dates, revisions and all
timing fields stay loaded. Explicit access can still load historical data; nothing
is deleted or rewritten. POSTs, provider jobs, background work and NeoScorpion do
not opt in. Existing request caches and commit/rollback invalidation are unchanged.
The lifecycle join is also restricted to the named read-only state endpoints.

The Sort CTE uses the production-verified unique index
`uq_neosektor_sort_states_gateway_date_sort`. A missing row produces SQL NULL,
matching the old empty set; child reads remain indexed scalar predicates. One
SQL statement retains its MVCC snapshot. No ID survives into another request.
An earlier CTE/IN semi-join experiment reduced scans but ran slower and was rejected;
the released scalar variant was measured independently.

NeoErmac retains explicit flight IDs, repeated-destination separation, linked pulls,
Door supervision, current-Sort ownership and signed stale-field protection. Its
Door persistence operation and lifecycle refresh selection are intentionally different;
their remaining reads were not blindly merged. Concurrent PostgreSQL Lineup edits
from the same original snapshot yield one success and one HTTP 409 atomically.

No operational algorithm, inventory, staffing history, permission, schema, index,
extension, hosting setting, background worker or refresh interval changes.

## Actual production observations

Configured Sektor intervals remain Driver/EBM/WBM/Conductor 5 seconds and Counts/
Discharge 15. Ermac remains shared fallback 10, Upcoming 15, Door/Outbound 30 and
Lineup 60 seconds. Production settings were inspected read-only.

A fully paginated November 4 06:00–06:10 UTC log sample contained 673 matched
access entries; 42 were MotherBrain calls matched through a Sektor referer and are
excluded from the following application attribution:

| Endpoint | Requests | Logged body bytes | Average / maximum | Status |
|---|---:|---:|---:|---|
| Sektor Driver version | 268 | 32,964 | 123 / 123 | 200 |
| Sektor Driver state | 120 | 177,542 | 1,480 / 5,027 | 97×200, 23×429 |
| Sektor Ballmat state | 105 | 250,147 | 2,382 / 5,136 | 200 |
| Sektor Conductor state | 105 | 179,676 | 1,711 / 5,147 | 200 |
| Sektor Counts state | 1 | 4,895 | 4,895 / 4,895 | 200 |
| Ermac Door state | 1 | 54,949 | 54,949 / 54,949 | 200 |

This sample is not sufficient to infer authenticated session counts, all Ermac
screen populations, or how many responses were unchanged from length alone.
The Driver state 429s are real; sharing hints does not prove that all operational
rate limits disappear. Existing thresholds remain authoritative.

Render already returns gzip for observed public dynamic/static text responses
(`/login`: 2,778 raw compressed bytes; Dispatch JS: 25,012). No second compression
layer was introduced. Logged application body bytes and billed network bytes are
different measures.

Cumulative counters remain investigation leads: Sort states 2,531,150 sequential
scans with 81 estimated live rows; waves 670,478 with 162; settings 598,461 with
one; open bays 403,245 with 140. These are not attributed to individual endpoints.
No reset, extension installation or production EXPLAIN ANALYZE was performed.
Exact SQL counts and plans below come from disposable databases.

Render metrics cover November 3 00:00 through November 5 02:00 UTC. Peak hourly
bandwidth was 853.5678 provider `mb`, CPU 0.15071337 provider CPU units and memory
364171260 bytes across different deployments/instances. The evidence includes the
hourly series.
Returned UTC-day bandwidth sums (provider `mb`, partial final day): 2026-11-03 = 3795.48246, 2026-11-04 = 3574.28008, 2026-11-05 = 24.8646. These remain service-wide measurements.
These mixed service-wide periods lack matched mission/user/session
normalizers. No comparable post-release busy Sort exists yet; production bandwidth,
Neon transfer, CU-hours and cost savings are **not verified or claimed**.
Neon consumption/per-query telemetry is unavailable through the connected tools;
the previously checked installation lacks `pg_stat_statements`. Legitimate five-
second operational reads prevent suspension while active. No scheduled FROST,
archive, Google or employee-recovery work was disabled to manufacture idle time.

## Equivalent isolated measurements

Fixtures use current configured Sort windows, real gateway/user authority, one
operational Sektor state plus up to 300 historical states, and Ermac workloads of
3 then 25 distinct mission/master identities (including repeated destinations)
and 100 synthetic employees. Provider responses are blocked. Before query functions
come from the starting Git source; current identical templates are used in both
query comparisons. Initial HTML changes only through the necessary asset version
URLs. Seven-request medians are reported; allocation is measured separately.

### SQLite

| Unchanged state | SELECTs | Request ms | SQL ms | Bytes |
|---|---:|---:|---:|---:|
| /neosektor/driver-routing | 11 → 10 | 5.143 → 4.825 | 0.404 → 0.4 | 651 → 651 |
| /neosektor/tunnel-conductor | 11 → 10 | 5.127 → 5.013 | 0.389 → 0.419 | 653 → 653 |
| /neosektor/ballmat EAST | 10 → 10 | 5.511 → 4.826 | 0.47 → 0.389 | 640 → 640 |
| /neosektor/ballmat WEST | 10 → 10 | 5.111 → 4.911 | 0.437 → 0.39 | 640 → 640 |
| /neosektor/live-counts | 10 → 10 | 5.071 → 4.909 | 0.443 → 0.404 | 648 → 648 |
| /neosektor/discharge | 6 → 6 | 2.948 → 2.706 | 0.243 → 0.219 | 646 → 646 |
| /neoermac/door-view | 12 → 11 | 4.752 → 4.827 | 0.364 → 0.4 | 367 → 367 |
| /neoermac/building-lineup | 10 → 9 | 3.494 → 3.66 | 0.293 → 0.321 | 367 → 367 |
| /neoermac/upcoming-pulls | 10 → 9 | 3.352 → 3.417 | 0.278 → 0.317 | 367 → 367 |
| /neoermac/view-outbound | 10 → 9 | 3.851 → 4.085 | 0.294 → 0.339 | 367 → 367 |

### Loopback PostgreSQL, after owned test jobs completed

| Unchanged state | SELECTs | Request ms | SQL ms | Bytes |
|---|---:|---:|---:|---:|
| /neosektor/driver-routing | 11 → 10 | 9.832 → 8.281 | 4.249 → 3.26 | 651 → 651 |
| /neosektor/tunnel-conductor | 11 → 10 | 8.806 → 9.357 | 3.699 → 3.634 | 653 → 653 |
| /neosektor/ballmat EAST | 10 → 10 | 8.775 → 9.259 | 3.658 → 3.522 | 640 → 640 |
| /neosektor/ballmat WEST | 10 → 10 | 9.408 → 8.145 | 4.03 → 3.195 | 640 → 640 |
| /neosektor/live-counts | 10 → 10 | 8.416 → 8.146 | 3.61 → 3.113 | 648 → 648 |
| /neosektor/discharge | 6 → 6 | 4.606 → 4.697 | 1.702 → 1.761 | 646 → 646 |
| /neoermac/door-view | 12 → 11 | 8.635 → 10.32 | 3.444 → 4.167 | 367 → 367 |
| /neoermac/building-lineup | 10 → 9 | 6.066 → 6.473 | 2.342 → 2.522 | 367 → 367 |
| /neoermac/upcoming-pulls | 10 → 9 | 5.944 → 6.596 | 2.286 → 2.416 | 367 → 367 |
| /neoermac/view-outbound | 10 → 9 | 7.087 → 7.589 | 2.817 → 3.046 | 367 → 367 |

The measured warm read requests retain one connection checkout/transaction and zero writes.
Counts/Ballmat/Discharge query totals stay unchanged, but unused snapshots are
excluded and Sektor revision SQL performs less repeated physical work. Driver/
Conductor and configured Ermac state reads each eliminate one SELECT. Snapshot
selection falls from one statement to zero, and Door View from two to zero.
The 25-mission Ermac workloads keep constant SELECT counts as identities grow.
Full measurements, CPU, allocation/RSS and repeated SQL shapes are in JSON.
Repeated shapes do not imply equal parameters: the separate previous/current
weekday matrix reads remain legitimate and were not removed.

Unchanged responses skip canonical state builders and Jinja before and after;
there is no newly invented incomplete fingerprint. Changed payload sizes remain
unchanged. Initial Jinja costs and context costs are measured, but no rendering
speedup is claimed: this package targets requests, database lookups and overfetch.
Context timing includes its own SQL and may overlap Jinja; these stages must not
be added. Windows CPU accounting is quantized; a zero batch value is below
resolution, not proof of free CPU. RSS snapshots are process-level observations,
not isolated allocation deltas. Request timing is variable and not uniformly lower.

Final alternating seven-run PostgreSQL plan evidence with 300 historical states:

`{'physical_sort_scans_before': 8, 'physical_sort_scans_after': 1, 'execution_ms_before': 0.181, 'execution_ms_after': 0.118}`

This is an isolated execution measurement, not a production CPU-saving percentage.

Two visible same-session Driver tabs over equal 6.5-second windows:
8 → 4 hint GETs, 984 → 492 body bytes, and 2 → 2 canonical safety requests.
The committed routing change appeared in 1,377.43 ms; the remaining tab continued
after the first closed. Mobile at 390px had no horizontal overflow. An additional
JavaScript scenario shares 30 hint reads as 10 across three tabs over ten cycles,
without sharing private data. Other users/gateways/Sort URLs remain isolated.
These synthetic results do not establish a production savings percentage.

## Verification and preservation

* 483 unique SQLite/Python IDs passed across the 482-case focused run and final
  core rerun (one newly added scope-header case). The larger run had 629 passing
  subtests; those are not added to unique-ID counts.
* 11 PostgreSQL IDs passed, including exact fingerprint/timer/missing-state
  equivalence, snapshot preservation, independent readers, committed changes and
  concurrent Lineup stale-edit rejection. Schemas were private synthetic UUIDs;
  existing databases/directories were preserved.
* 126 JavaScript tests passed: stale/out-of-order responses, dirty fields,
  permission/session changes, unsupported coordination, leader loss, hidden/
  resume, network errors, Retry-After and unaffected NeoScorpion behavior.
* Two browser test methods passed: Sektor Chromium multi-tab routing and Ermac's
  canonical identity/pull workflow in desktop Chromium and mobile WebKit (two
  engine subtests). Screenshots and receipts are retained in the ignored evidence
  directory. No production test mutations were used.
* Compilation, JavaScript syntax and `git diff --check` passed. Existing warnings
  remain visible. No tests were deleted, skipped or marked expected-failure.

Existing SQL assertions were adjusted only for attributed lower budgets, newly
targeted read endpoints and treating WITH as a SELECT round trip. The initial
test run exposed five existing query-test IDs/ten failure events; functional
payload/permission assertions remained intact. New fixture issues (warm initial
page defaults and a mistaken model field in the race test) were corrected without
changing production behavior. The full-system suite was not run.

All twelve unrelated untracked-file hashes match the saved preservation inventory.
No existing Codex job, session, history, worktree or unfinished work was stopped or
cleaned up. Production missions, staffing, settings, Google Sheets, archive and
historical evidence remain unchanged. No synthetic production operations occurred.

## Remaining work and release

The largest remaining opportunity is safely coordinating equivalent *state*
checks across tabs without sharing operational data or stale permission/window
metadata. This package intentionally shares only the already-supported opaque
hint path. Initial Lineup option markup remains large, and Outbound first full
responses still need complete state; existing common/row-delta protocols remain.
Different Door/lifecycle selection rules require a dedicated equivalence proof
before any further candidate reuse. Persistent idle connections/compute savings
cannot be diagnosed from cumulative table counters or this short rollout alone.

Release is the main commit containing this report, through existing Render
AutoDeploy. Exact resulting LIVE SHA/deployment, startup, health/login, CSP/HSTS,
CSRF, published hashes and authenticated read-only screens are checked after
deployment and recorded in the completion receipt and final response. No manual
deployment or infrastructure change is used.

Evidence: `2026-11-04-neosektor-neoermac-performance-evidence.json`.
Local scripts/logs/screenshots: `instance/sektor-ermac-performance-20261104/`.
