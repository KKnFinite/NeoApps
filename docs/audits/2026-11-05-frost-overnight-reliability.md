# FROST overnight scoring reliability

Starting main/LIVE: `a50bb31a5c6bbe5d54fd33fbdea7ff238aac3d1e`.
Production baseline read at November 5 05:04–05:16 UTC; no test captures triggered.

## Actual coverage before this release

| Central hour, November 4 | UTC slot, November 5 | Actual result |
|---|---|---|
| 20:00 | 02:00 | 8/8, archived; outside the newly enforced 21:00 opening |
| 21:00 | 03:00 | 0/8, weather archive verified; HRRR resource rejection |
| 22:00 | 04:00 | No committed predictions; expired after 42 resource deferrals |
| 23:00 | 05:00 | Ongoing retry, 14 deferrals at 05:16; not a completed failed hour |

The two completed eligible hours had no scored model set. The 20:00 reference
is not included as an eligible success. These receipts are factual coverage,
not a prediction-accuracy evaluation or an estimate of an entire future night.

## Confirmed causes

Every operational request previously aborted the many-step HRRR acquisition.
Restarting from the beginning made a roughly 75-request source acquisition
unlikely to finish among frequent operational refreshes. Logged post-restart
attempts lasted about 1.2–9.9 seconds before stopping. A deployment also interrupted
an owned capture: existing crash fencing delayed recovery to about 04:16 UTC.
That initial lease delay does not explain the subsequent 42 deferrals.

Memory protection was real: the 03:00 end-of-attempt container sample was
370,790,400 bytes (353.6 MiB). With a 512 MiB container, HRRR's unchanged hard
guard plus 32 MiB reserve permits at most 352 MiB at admission. This sample is
after the attempt, not an exact measurement of its rejected allocation. Render
averages and individual-process RSS must not be confused with cgroup usage.

Repeated raw coordinate/time chunks were independently archived/read-back
verified for every source URL despite having identical hashes. Native vectors
and observation summaries were also rebuilt for both models at each horizon.
Those repeated allocations/I/O prolonged the interruption window.

Finally, collection activity and a verified weather archive could look successful
even when no model scored. They are separate facts.

## Corrections

* Only the existing background auto-capture cooperatively pauses at checkpoints.
  A condition wait releases its lock and lets foreground requests finish. Total
  waiting is capped at 20 seconds per attempt; CPU pressure/cooldown checks remain.
  Foreground requests and other jobs remain fail-fast. No DB transaction spans
  this wait. A closing hour/window stops new source work. The 90-second HRRR
  deadline and all transfer/decoder limits remain; deadline exhaustion after
  yielding remains retryable rather than masquerading as missing scientific input.
* A capture-local hash-to-verified-key map reuses only successfully stored and
  read-back-verified identical originals. URL/time/provenance receipts and actual
  source bytes remain intact. No global cache or borrowed client is introduced.
  The equivalent original-format fixture retains 67 source-body GETs but reduces
  verified private archive operations from 67 to 28. This is synthetic I/O evidence,
  not a production bandwidth/cost-saving percentage.
* Eight model evaluations reuse four immutable horizon vectors and one common
  observation-window check. Exact artifacts, input validation, scores, horizons,
  thresholds and physical-frost disclaimers remain unchanged. A redundant decoder
  split reference is released. One additional GC attempt can reclaim closed SDK
  cycles at failed HRRR admission; it never changes an admission threshold.
* Capture receipts retain compact wait/source-reuse metrics and failed memory
  usage/limit diagnostics. Status explicitly separates weather capture, experimental
  scoring and archive verification. A 0/8 archive says UNAVAILABLE 0/8 and
  `prediction_ready=false`; pending/leased work is not scored. The last complete
  score set has a distinct timestamp. No operational DUCC alerts are added.

The existing 15-minute crash lease and token fencing remain. An active owner is
not stolen; a superseded token cannot finalize a newer attempt. A deployment can
still delay an interrupted capture until that lease expires. No production lease
reset, historical rescore or record repair is performed.

## Validation and release evidence

36 unique focused Python IDs passed; six passing subtests are counted separately.
Coverage includes real foreground-request yielding/resume, bounded cumulative
waits, request self-deadlock prevention, hour closure, database-free checkpoints,
lease fencing, source-byte redecoding/read-back, eight frozen model contracts,
0/8 status, archive retries without recalculation, hourly identity/DST, permissions,
strict memory reserve and absence of scientific runtime imports. Existing warnings
remain visible; no tests were skipped, weakened or marked expected-failure.
Compilation and diff checks pass. No broad suite, browser sweep or load test.

Database-configured RFD windows/days/timezone, immutable historical evidence,
historical-only frozen training, artifact registrations, operational records,
hosting configuration and unrelated files/jobs remain unchanged. No retraining,
model promotion, heuristic fallback, new service or memory-limit increase.

The implementation commit containing this report is released by Render AutoDeploy.
Exact LIVE SHA/deployment, startup, health/login, authorized FROST status and frozen
model hash checks are recorded in the completion receipt/final response. Actual
post-release natural capture receipts are reported separately with their cutoff
time; deployment-partial hours are identified. No manual capture is used to create
after evidence. Sustained reliability is not established by tests or one capture.

Safe headroom, source publication and complete valid observation windows remain
necessary. If operational work leaves no safe research interval, scoring must
defer or remain unavailable rather than exceed memory or fabricate a prediction.
Local evidence: `instance/frost-reliability-20261105/`.
