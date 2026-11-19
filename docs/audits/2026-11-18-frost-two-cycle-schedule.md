# FROST opening and midpoint schedule

Starting source: `4b52796487f2b5fd6a5d87b1f29c77105bcccc86`.

## Schedule and preservation

FROST acquisition/scoring now has two scheduled instants per configured Sort:
opening and the midpoint of the actual elapsed UTC duration between the configured
local boundaries. Timezone, operating weekdays and Sort (not OPS) windows still
come from MotherBrain's existing database settings. RFD currently resolves to
21:00 and 01:00 Central for its Monday–Thursday 21:00–05:00 windows. No application
code contains those times/days.

Each cycle retains at most one hour for bounded retries, shortened by the next
cycle or Sort end. Spring/fall DST midpoints use the real seven-/nine-hour Sort
duration; they are not a hardcoded four-hour offset. Exact minute/second cycle
identities prevent collisions for non-hour boundaries. Coincident cycles reuse
one capture. Distinct overlapping Sorts do not activate a future Sort early.

The existing heartbeat plans opening/midpoint deadlines rather than intermediate
hours. Unchanged/closed-cycle calls do not claim work, write records, download
weather, score models or upload snapshots. Cross-process worker/capture fencing,
foreground-request priority, memory admission and bounded attempt counts remain.
Committed archive retries can complete outside capture windows without reacquiring
or rescoring evidence. Uncommitted expired attempts are projected as missed on
reads; historical READY captures are not rewritten or backfilled.

New receipts record their schedule and precise acquisition deadline. Gap counting
counts only scheduled cycles, excluding intervening hours/closed days. Current-Sort
coverage returns two expected cycles with due/captured/fully-scored/missed counts.
Historical hourly receipts and historical aggregate counts remain intact.

DUCC HTML, CSS, labels and JavaScript are unchanged. Normal weather acquisition and
hourly forecast/live refresh remain independent of model execution. Existing score
projection accepts exact cycle identities and includes the bounded retry allowance
when looking back for genuine H6 results; it never interpolates missing hours.
Only existing research-status wording changes from hour to cycle.

Eight frozen models, H0/H1/H3/H6, artifacts, learning eligibility and automatic
decision safeguards are unchanged. Read-only production verification found display
ON, live learning OFF and automatic retraining OFF (settings revision 3); this
release does not write those settings or create operational production test data.

## Equivalent isolated eight-hour measurement

Synthetic verified model contracts, mocked providers/storage, same eight hourly
worker invocations and observation inputs; model registration excluded from counts.

| Metric | Before | After |
|---|---:|---:|
| Weather acquisitions / eight-model batches | 8 | 2 |
| Actual scored outputs | 64 | 16 |
| Snapshot PUTs | 8 | 2 |
| SELECT statements | 208 | 70 |
| INSERT statements | 10 | 4 |
| UPDATE statements (including zero-row updates) | 80 | 22 |
| Compressed snapshot bytes | 21,829 | 5,459 |
| Local elapsed seconds | 1.333 | 0.358 |

This demonstrates 75% fewer model batches/snapshot transfers and 66.3% fewer
SELECTs in this fixture. It is not measured Render bandwidth, HRRR transfer,
Neon CU/cost savings or a promise of lower peak memory. Memory thresholds remain
unchanged and resource contention may still prevent a cycle from scoring.

## Focused verification

- 52 unique SQLite/Python cases: 45 final scheduling/display/memory/priority/base
  capture checks plus seven affected real-timer regressions. Covers spring/fall
  DST, non-hour midpoints, actual eight-model sets, duplicate attempts, failed and
  expired captures, missed cycles, archive-only recovery and no between-cycle writes.
- Four PostgreSQL cases: real heartbeat wakeup, two-process single ownership,
  archive-outage recovery with immutable timestamps and two-cycle idempotency.
  Process ownership/idempotency were rechecked after the final schedule review.
- Chromium and WebKit each passed the focused normal DUCC/TV/mobile live-refresh
  workflow; ON/OFF visibility and node-Master read-only permissions are preserved.
- Existing DUCC JavaScript revision/draft/conflict harness, Python parsing and
  `git diff --check` passed. Existing warnings were not suppressed.
- Local evidence: `instance/frost-two-cycles/` (ignored). No broad system suite,
  production synthetic capture, migration, artifact change or unrelated cleanup.

Release uses existing main AutoDeploy. Exact LIVE SHA/deployment, health/startup,
authorized status and unchanged setting verification are recorded in the completion
report. Sustained production cycle coverage requires observing subsequent real Sorts.
