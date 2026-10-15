# Resource-aware PROF v4

Starting main: `177d7c5a4bb3f6edf5120c5fdf066f34a87240d5`.

## Attribution and historical policy

- Existing v3 snapshots and in-progress v3 jobs retain their original formulas,
  assignment-only setup rule, settings, and physical ON/OFF history.
- New physical starts freeze v4 resource evidence in the existing Work performance
  context; finalization freezes it in the immutable FuelingEvent snapshot.
- v3 Truck: `100 * pump minutes / physical ON-to-OFF minutes`.
- v4 Truck: `100 * (travel allowance + pump minutes) / (eligible start-to-OFF - proven waiting)`.
- Fueler: `100 * (zero-or-full setup allowance + pump minutes) / physical ON-to-OFF`.
  v4 setup uses available, accessible, documented preparation, not assignment lead alone.
- Aggregate ratings sum each qualified job's original expected and actual components;
  they never average job percentages. The report/COPY/CSV identify each policy version.

Example: 3,000 gallons / 150 gpm = 20 pump minutes. Prior truck OFF at 00:10,
assignment at 00:05, eligible start at 00:10, physical ON 00:30, OFF 00:40,
and two configured travel minutes gives Truck `100 * 22 / 30 = 73`.
Qualified advance preparation gives Fueler `100 * 20 / 10 = 200`.
With full eight-minute setup, Fueler expected minutes become 28 instead.

## Evidence and limitations

Truck opportunity is bounded by effective assignment, previous physical OFF,
aircraft early-staging policy, effective destination parking assignment, and
recorded master/nightly availability. Physical origin comes from the previous
physical segment, not departure ordering or a later assignment label.

Fueler work-end requires physical OFF, final valid Actual readings, and a validated
final-data milestone belonging to that physical Fueler/tail. Dispatcher COMPLETE
is not a resource-release timestamp. Unknown historical work-end stays unknown.
Preparation may be documented while the assigned truck remains physically busy.

There is no separate confirmed pre-ON truck-arrival/waiting milestone in the
existing operational records. Expected travel is not measured arrival. Consequently
no waiting is guessed or subtracted: affected Truck jobs are unqualified with an
explicit reason. Physical ON cannot be relabeled as waiting. This release does
not add an arrival control or new hard fueling restriction.

Missing previous location/release, missing availability continuity, overlapping or
untrusted visits, intervening holds/OOS/TOP OFF/DEFUEL uncertainty, or ambiguous
future resource order produce explicit exclusions/uncertain forecasts. First jobs
without a trustworthy previous physical origin cannot qualify for Truck v4.
History queries are bounded; exceeding the bound fails qualification closed.

## Persistence and safety

Two nullable additive JSON columns, both named `availability_history`:

- `neoscorpion_fuel_trucks`: future master active/OOS observations.
- `neoscorpion_sort_trucks`: future nightly availability/status observations.

Normal idempotent NeoScorpion/bootstrap and SQLite/PostgreSQL optional-column
conventions add them. No backfill, rewritten history, new inventory movement,
new table, or data deletion. Existing locked actions append actor/source/time
metadata only. Missing legacy history remains NULL and uncertain.

Append-only Work IDs, A-to-B-to-A visits, event uniqueness, inventory epochs and
Dispatcher-only routine completion remain unchanged. No Truck Rolldown source,
driver workflow, Dispatch sorting/scrolling, tank/APU math, or inventory algorithm
was changed. Evidence-capture failure excludes performance rather than blocking ON.

## SPEAR and learning

Canonical shared resource evidence feeds the existing physical OFF forecast.
Travel is included once before ON; physical ON anchors the service goal without
adding travel again. Truck physical release and Fueler final-work release remain
separate. Forecast finishing allowance belongs to Fueler availability, not Truck
OFF. Multiple unproven future preassignments are not given simultaneous precise
availability. Zero configured staging/travel is respected.

v4 forecasts accept qualified v4 observations only; OBSERVE stays baseline and
APPLY still requires three samples. Ratings are not multiplied together. Setup
credit is not a measured labor duration or mechanical pump rate.

New resource-aware Vault outcomes use strict `spear-learning/v3` payload validation;
old v1/v2 payloads/keys remain accepted unchanged. Unknown resource evidence cannot
silently become eligible training. Capture controls, deduplication, and failure
isolation remain in place.

## Focused validation

- 22 exact math/evidence/forecast unit tests: PASS.
- 7 operational integration tests: PASS (one intentionally injected evidence failure
  logs an error while asserting the physical action still succeeds).
- 5 disposable PostgreSQL resource tests: PASS, including repeated bootstrap,
  preserved v3 IDs/timestamps/gallons/snapshots, concurrent COMPLETE, one posting,
  A-to-B-to-A, and independent transfers.
- 2 existing PostgreSQL Truck Rolldown tests: PASS.
- 5 existing Truck Rolldown tests: PASS.
- 6 selected existing SPEAR planning tests, 3 physical-start safeguards, and 3
  existing ForecastMath tests: PASS.
- Chromium and WebKit report tests: PASS at 1440x900, 1920x1080, 390x844, 375x568.
  Checked actual report route, historical selector, v3/v4 math, COPY, CSV, filters,
  browser errors, bounding widths, and no horizontal page overflow.
- Python compileall, existing report JavaScript syntax, and diff-check: PASS.

Baseline-only failure (not changed):
`FuelingStartHotfixTest.test_preassigned_truck_release_travel_and_expiry_never_auto_create_on`
expects the old exact `TRUCK STILL FUELING` wording, while production already displays
the `WAITING FOR TRUCK` prefix. Reproduced against the starting production service
source in memory. This is not a new resource-aware regression; its assertion was
not weakened or altered.

Screenshots are ignored local evidence in
`instance/browser-evidence/resource-prof-final-chromium/` and
`instance/browser-evidence/resource-prof-final-webkit/`.

Deployment verification is recorded in the delivery response only after the exact
pushed commit reports LIVE, schema bootstrap succeeds, and `/healthz` returns 200.
