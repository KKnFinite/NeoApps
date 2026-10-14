# NeoScorpion physical fueling, PROF, and SPEAR

## Legacy live-visit migration identity — focused blocker resolved

Implementation and release-validation notes. Exact deployed commit and runtime
status must be verified separately against Render; this document is not itself
deployment evidence.
The specific PostgreSQL release gate
`test_legacy_live_visit_migration_does_not_reuse_a_closed_event_owner` was
reproduced failing with `1 == 1`, then fixed without changing its identity
assertion. The old assignment/tail container is not a permanent live visit:
if a closed event owns it and the assignment is live, migration retains the
historical Work ID and allocates a separate current Work ID after replacing
the obsolete uniqueness constraint. Original event, tank, snapshot and audit
foreign keys remain unchanged. No inventory posting occurs during migration.

A later manual ON is carried forward only when timestamp, cycle, truck and
Fueler prove the later visit. Uncertain legacy timestamps and measurements
remain on the retained historical container, flagged for review. Stale tanks
and assignment-wide T/F are never copied into the new visit. Transfer review
survives a new TRUCK ON and requires explicit dispatcher confirmation. Sealed
historical owners no longer masquerade as live physical truck occupations;
their recorded event releases remain available to the existing travel logic.

Focused validation for this correction: 16 isolated PostgreSQL tests passed,
including repeat and concurrent bootstrap, independent reviewed 1,200-gallon
posting beside the preserved 3,000-gallon event, duplicate completion, rollback,
and A -> B -> A with same/different trucks. Seven adjacent segment/SQLite
compatibility tests passed. Compileall and diff-check passed. The earlier
157 Python / 9 JS / 2 browser runs are intermediate draft results, not final
release validation against this correction.

Final local integration passed 157 core Python tests, 16 PostgreSQL tests and
two actual-control browser workflows at 1440px desktop / 390px mobile. The two
Batch 3-specific audit assertions were then corrected: three focused tests
verify existing fuel-entry audits, no-op correction, failed END EARLY rollback,
changed-field-only deltas, and duplicate-save audit/event prevention. Only a
test-clock chronology fixture required correction during final integration;
production safety checks were not relaxed.

### Preexisting baseline failures (not Batch 3 regressions)

The adjacent-suite comparison identified 20 remaining Python failures/errors
that also reproduce on production commit
`1869920c30e212613430bc9cfe122c6ccadb7252`. They include old positive-T/F-without-
truck fixtures, invalid ON/OFF fixture chronology, an unverified legacy ON
display expectation, and schema-contract expectations predating Batch 2's
inventory metadata. These were not skipped or weakened to obtain a green run.

The same seven JavaScript failures reproduce unchanged on that production
baseline in `tests/js/neoscorpion_dispatch_workflow.test.js`: the old test
initialization API and template-text checks that target the full Dispatch
template rather than its existing extracted panel. Relevant fuel-data/status
and cancellation tests pass; the failed baseline checks remain documented.

Release uses the configured `python scripts/bootstrap_database.py` pre-deploy
bootstrap and Render AutoDeploy only. No artificial production fuel mutation
is part of verification.

TRUCK ON and TRUCK OFF are physical milestones. OFF does not require final
Actuals/APU/T/F. A released truck is free physically; its Fueler still owns the
pending final data. Saving valid final data or recording OFF after valid data
automatically completes the routine cycle in the same transaction. Holds,
direction, aircraft, APU, tank validation, DEFUEL capacity, and FOB separation
still apply. Dispatcher completion remains for exceptions. Existing operation
row locking serializes mutations, and critical rows are freshly loaded under
their locks; stale resource/cycle and field baselines remain protected.

## Inventory while final data is pending

OFF records the truck's inventory epoch and makes the reading unverified. A
pending marker distinguishes unresolved usage from a proven discrepancy.
Known transfers are posted once against the retained reading; multiple pending
jobs may finish in either order. Inventory becomes verified only when all usage
in that epoch is resolved and no discrepancy exists. A newly confirmed physical
reading advances the epoch. A later completion of work already OFF before that
reading records its event without subtracting that transfer again. Verification
is blocked during confirmed active physical fueling. SHORT/unknown inventory
does not block valid FUEL/UPLIFT completion or fabricate a balance; DEFUEL still
requires known capacity accounting. TOP OFF/SUMP confirmation advances the same
epoch. SPEAR cannot use an unverified reading as fuel availability.

## Retained job mathematics

New physical starts snapshot configured pump rate, Setup Time, Advance Setup
Window, actual assignment timestamps, resources, cycle, and ON source. No old
assignment/ON/Actual timestamp is synthesized. Resource/cycle resets preserve
completed event snapshots; ambiguous interrupted segments are excluded.

* Pump minutes = actual T/F gallons / snapshot aircraft pump rate.
* Physical minutes = exact UTC ON-to-OFF seconds / 60.
* Fueler allowance is zero when assignment lead >= configured advance window,
  otherwise the full Setup Time. An unset window conservatively uses full setup.
  Preparation readiness does not decide this policy credit.
* Fueler expected = allowance + pump minutes; Truck expected = pump minutes.
* PROF = 100 * sum(expected minutes) / sum(physical minutes), rounded for display.
* Actual wrap-up = max(Actuals-received - OFF, 0). Other administrative delay is
  completion minus max(OFF, Actuals-received). Neither is in physical PROF.

Reports use immutable snapshots rather than today's settings. Legacy records
show available facts and explicit exclusions. Reports at `/neoscorpion/reports/prof`
offer sort/resource selection, 50-job pages, component sums, plain-text copy, and
CSV. A sort query has a 5,000-job bound with an explicit truncation notice; the
sort picker contains the latest 200 operations. CSV includes gateway-local dated
24-hour timestamps and exact elapsed seconds, with safe spreadsheet text fields.

## Two physical OFF forecasts

The shared SPEAR forecast service powers Dispatch, Fueler, and active resource
forecasts. 100 PROF OFF = physical ON + allowance + expected pump time. Before
ON it uses recorded aircraft readiness, assignment times, actual release/travel
eligibility, or a trustworthy current-job release forecast. Conflicting future
ordering and missing timing remain unknown. No forecast slides with render time.

LIKELY uses reliable, snapshot-qualified outcomes from a bounded 90-day / latest
2,000-event history. Each matching aircraft event is included once with weight:
same Fueler+Truck 4, Fueler 3, Truck 2, aircraft-only 1. Its observation is actual
service minutes / that job's expected Fueler minutes. APPLY requires at least
three qualified events and blends with a baseline of three unit observations:
`factor = (3 + sum(weight * ratio)) / (3 + sum(weight))`, bounded to 0.5–2.0.
LIKELY = ON + current expected duration * factor. OBSERVE and insufficient samples
use baseline explicitly. Fueler and Truck PROF are never multiplied or treated
as independent overlapping pump-rate measurements.

Calibration labels service duration, measured preparation, assignment lead,
Actual wrap-up, and forecast error separately. ON-to-OFF does not train a
mechanical pump rate; setup policy is not measured preparation. Forecasts known
at mutation time are retained before OFF and copied to the immutable event.
Completed learning records use `spear-learning/v2`; legacy v1 remains readable.
Vault export still requires capture enabled and configured durable storage,
deduplicates at its existing boundary, and runs after commit. Capture/provider
failures cannot undo physical fueling or completion.
