# NeoScorpion performance optimization, Phase 2

Starting source and LIVE SHA: `89eea42e3d8062c3f9235570eb5f4676016a10fe`.
Starting Render deployment: `dep-dblqfv49v7es73brdm00`.

This release fixes demonstrated cleanup/export defects and reduces redundant
database work. It does **not** complete every possible panel optimization or
claim a measured reduction in production bandwidth/CU usage. Recommendations
remain ON, automation OFF, calibration OBSERVE and whole-sort optimization SHADOW.

## Sort-end cleanup gate

The initial existing suite produced 44 passes and six failures: three test methods
in both SQLite and PostgreSQL still advanced six minutes despite the recently
introduced six-hour retry for old decisions. Their clocks now reach the real
retry deadline; finalization and immutable-outcome assertions remain intact.

Additional tests independently reproduced two real deletion defects before the
fix: a confirmed physical visit with no completed event was deleted, and an
attempted/unverified upload lost its durable receipt. Review also found an
unlocked reason-insertion race with parent deletion.

The corrected cleanup:

* Computes closed Sort dates from configured local windows, conservatively
  handling ambiguous and nonexistent DST times. Missing configuration protects
  observations. Overnight windows and active Sorts remain protected.
* Uses indexed correlated EXISTS against physical visits and events before
  limiting candidates; protected old rows no longer occupy the entire batch.
  It hydrates at most 400 decisions and 2,001 child identifiers. An oversized
  child family is retained rather than partially deleted.
* Locks decisions with SKIP LOCKED, skips busy canonical assignment writers,
  rechecks physical connections after ownership, and locks derived receipts.
* Retains every attempted/unverified parent or child, even after an expired
  crash lease. A lost provider response remains uncertain until normal delivery
  verifies it. It never deletes R2 objects.
* Locks a decision before adding its optional reason, preventing an orphan
  created concurrently with pruning. Busy children preserve their whole family.
* Leaves assignments, events, tanks, inventory, completed learning and reports
  untouched. No schema migration, backfill or historical repair is introduced.

The expanded initial gate passed 64 SQLite/PostgreSQL/replanning cases. Subsequent
annotation and idle-drain checks also passed, including real independent
PostgreSQL transactions and an upload paused past its lease.

## Live-screen SQL reductions

Board, Driver, Hanzo revision endpoints and all five private panel endpoints now
reuse the established fresh request-local gateway/membership/app/node authority
projection. This is not an authorization bypass or public allowlist change.
Revoked roles are checked again on every request; GET/HEAD cannot provision
legacy access rows. Unsafe methods retain their existing path.

Aircraft configuration and parking rows are reused only within one GET/HEAD.
Existing commit/rollback hooks invalidate the inputs. Canonical POST mutations
retain fresh reads. No shared HTML or cross-user permission cache was added.

Fueler, Board, Driver and Hanzo OBSERVE timing no longer load the learning history
and calibration review that those views do not display. Their rendered panel
digests match the full-review version. SPEAR/Dispatch still calculate the learning
review; APPLY and canonical forecast capture still load verified history.

Synthetic 25-aircraft measurements, using the same warm fixture on each side:

| Response | SELECTs before / after | Bytes before / after |
| --- | ---: | ---: |
| Full Dispatch | 84 / 77 | 543,001 / 543,001 |
| SPEAR fragment, unchanged | 64 / 57 | 2,875 / 2,875 |
| Critical completion, first fragment response | 71 / 66 | 523,201 / 523,201 |
| Fueler, unchanged | 46 / 38 | 175 / 175 |
| Board, unchanged | 45 / 37 | 151 / 151 |
| Driver, unchanged | 51 / 43 | 151 / 151 |
| Hanzo, unchanged | 39 / 31 | 151 / 151 |

A separate fresh-request comparison executes the starting implementation in
memory, clears request state and returns the connection after each request:

| Response | SELECTs before / after | Median ms before / after |
| --- | ---: | ---: |
| Full Dispatch | 88 / 78 | 51.35 / 48.21 |
| SPEAR, unchanged | 68 / 58 | 35.33 / 32.30 |
| Fueler, unchanged | 50 / 39 | 27.73 / 24.28 |
| Board, unchanged | 49 / 38 | 20.73 / 16.82 |
| Driver, unchanged | 55 / 44 | 19.74 / 16.53 |
| Hanzo, unchanged | 43 / 32 | 18.69 / 14.76 |
| Board revision | 15 / 8 | 3.90 / 2.49 |
| Driver revision | 15 / 8 | 3.98 / 2.46 |
| Hanzo revision | 15 / 8 | 3.96 / 2.47 |

Each fresh request retains one read transaction and connection acquisition and
zero commits. This legacy-access fixture previously attempted one rolled-back
`portal_app_accesses` INSERT per cold request; afterward it performs no DML.
This does not establish that every production user had that legacy condition.

## Background activity and completed-learning integrity

An empty SPEAR drain previously used seven SELECTs, two transactions, two
connection acquisitions and two commits. It now uses the same seven SELECTs,
one transaction/acquisition, zero commits and one final rollback; the connection
is released. Empty fleet discovery no longer commits independently.

Final testing also reproduced a pre-existing archive defect using the starting
canonical implementation and unchanged validator: rolling-learning snapshots
contain `learning_origin`, which the Vault validator rejected. The validator now
accepts precisely `production`, `test`, or legacy null/missing values. Unknown
values remain rejected. The payload, checksum, training eligibility and archive
identity are not rewritten. Canonical v4 completion, mock-R2 read-back and
idempotent repeated exports pass. Existing pending intents can retry normally.

No scheduling interval, timer, FROST behavior or hosting setting changed. Real
SQL instrumentation of deterministic 60-minute simulations produced:

| Scenario | SELECTs | Connections / transactions | Writes | Commits | Longest DB-free gap |
| --- | ---: | ---: | ---: | ---: | ---: |
| Otherwise idle, hour already captured | 96 | 12 / 12 | 0 | 0 | 600 seconds |
| Required FROST capture at minute 30 | 131 | 29 / 29 | 11 | 13 | 600 seconds |
| Recovery expiration at minute 30 | 99 | 12 / 12 | 3 | 1 | 600 seconds |

Both existing clocks wake locally without SQL between their deadlines. Hourly
capture occurred at exactly simulated second 1,800. These existing 600-second
gaps exceed Neon's 300-second threshold; they are not new savings from this
release. Visible browser revision polling still legitimately requires database
access, even when operators make no changes.

Read-only production evidence: three unresolved SPEAR decisions were due later,
no pending SPEAR deliveries and no idle application connections in the sampled
snapshot. FROST 20:00, 21:00 and 22:00 UTC slots were ready. Nine older missed
slots remain historical evidence; nothing was reconstructed or rewritten.
Render recorded a 38.599-second FROST capture and roughly 89 MB RSS increase at
22:00, which is real scheduled work, not an idle poll to disable.

The available Render 20:00–22:00 UTC metrics cover quiet traffic and different
instances/deployments: reported bandwidth buckets 19.38055 and 0.010189056 `mb`,
sample memory 303,128,580 / 213,327,870 / 194,535,420 bytes and one CPU sample
0.004208266 provider CPU units. They cannot establish attributable savings versus
the busy November 3 sample. Sustained Neon active-time and transfer comparisons
were unavailable. Audit reads themselves can wake Neon.

## Focused validation and limits

Across the recorded runs, 190 unique SQLite/Python cases and 39 unique disposable
PostgreSQL cases passed. Repeated cases are counted once; passing subtests are
additional. Eight JavaScript tests and two essential Chromium workflows passed.
No cases were deleted, skipped or marked expected-failure. The full-system audit
suite was intentionally not run. Existing datetime deprecation warnings remain
visible. Compilation, JavaScript syntax and diff checks passed.

Chromium measured one SPEAR-only request, one critical completion request while
dirty, one clean full refresh and zero panel requests for unchanged polling.
SPEAR rendering took 65.60 ms and newly eligible COMPLETE appeared in 67.97 ms,
excluding the configured polling interval. Draft focus/text, mobile overflow,
Hanzo document identity and a complete Fueler A-to-B handoff were preserved.
The latter retained one visit and one event with the original ON and actor.

Further optimization is explicitly unresolved: a revision-only panel early exit
needs a proven fingerprint covering mission/configuration inputs and clock-driven
warnings. Further per-mission replacement must also prove deletion, cycle/history,
open-dialog and draft safety. No shortcut was released without that proof. Clean
full Dispatch payloads remain large; this release adds no claimed byte savings
over the preceding fragment optimizations and does not change polling cadence.

Measurements are in `2026-11-04-neoscorpion-phase2-evidence.json`; detailed local
logs, screenshots and browser receipts remain under
`instance/scorpion-phase2-20261104/`. All 12 original untracked file hashes were
preserved. No production test fueling, Google/R2 test writes, record repair,
credential/configuration change, schema migration or job cleanup was performed.

Release uses main and Render AutoDeploy only. The exact final SHA, deployment ID
and post-release health/security/authorized-page results are recorded in the
completion message and the local release receipt after deployment.
