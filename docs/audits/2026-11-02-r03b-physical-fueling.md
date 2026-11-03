# R03B — physical fueling visits, cancellation and evidence

## Scope and decision

Starting `main`, fetched `origin/main`, and Render LIVE were
`cb7cf261a390ebc1de90505a4fb17d06c78b6c2f`; deployment
`dep-dbkitf3bc2fs73ftv1cg`. The checkout had no tracked edits. Six unrelated
untracked artwork/prototype/documentation items were preserved and excluded.
No AGENTS.md was found in the inspected repository instructions.

**Targeted integrity corrections and regression-fixture repairs; broader R03
remains open.** The six requested modules originally produced 38 failing and
45 passing case IDs. The accompanying [case register](2026-11-02-r03b-physical-fueling-evidence.json)
records every failing ID, its classification and final result. This package
does not declare a clean full application suite or authorize historical repair.

The release is the commit containing this report. Its final SHA, AutoDeploy ID
and observed LIVE status are reported after deployment; they cannot be embedded
in the commit before they exist. Local read-only release receipts remain under
ignored `instance/r03b`.

## Independently verified contract

The October 16 lifecycle and multi-truck contracts, current canonical services,
models, revision/authorization checks and the R03/R08 evidence were re-read.
New tests create physical evidence through the actual save, ON, OFF, submission,
Dispatcher completion, follow-up, unassignment and tail-confirmation services.

* Assignment and mission are operational owners. A physical WorkState ID is a
  permanent visit identity, with unique `(fuel_assignment_id, visit_number)`.
  The same aircraft can have multiple visits, including A→B→A. A NULL legacy
  visit number does not assert assignment/tail uniqueness. Event sequence and
  work identity remain separate; assignment event order is preserved.
* Saving readings is preparation/data entry, not physical ON. OFF releases the
  truck but is not Fueler COMPLETE ASSIGNMENT or Dispatcher COMPLETE. A live ON
  cannot be silently ended by a swap/unassignment. Retired visits remain
  addressable by their exact work ID and sequence.
* A completed, current-operation, matching-physical-tail event can seed
  **Remaining only** on another mission's replacement visit. Its source event
  is retained in the `confirm_tail` provenance audit. Actual, T/F and fresh APU
  evidence are not manufactured; that carryover alone leaves FOB READY false.
  Different tails and foreign sorts cannot borrow these measurements.
* A cancel restores the completed parent projection and retains canceled visit
  facts and audit history. Positive transfer or mandatory transfer review in
  **any** visit of the current cycle prevents cancellation. Completed transfer
  events and truck inventory are never reversed to make cancellation succeed.
* A legacy display ON can remain a historical value without acquiring manual
  or confirmed ON provenance. The repaired legacy fixture verifies that it
  cannot manufacture a completed event without the required handoff.

## Application defects corrected

### 1. Cancellation could erase another mission's newer shared-tail projection

The old guard checked completed FuelingEvents only. Canonical Fueler saves
update shared `NeoScorpionTailFuelState` before such an event exists. A later
save for the same tail on another mission could therefore be replaced by the
canceling assignment's older rollback snapshot.

The guard now also checks the durable, operation-scoped `fuel_entry` audit,
joined to the exact physical work/tail, since follow-up creation. If another
mission has recorded newer evidence, cancellation returns the existing
`REVIEW REQUIRED: another mission updated ...` error before restoration. The
original event guard is retained. Notes are included conservatively because
the rollback snapshot also contains shared-tail notes. No evidence is removed
and no replacement ownership is guessed from tail alone.

This is one bounded existence query on a user-requested cancellation, using
existing operation/assignment indexes. No history scan, schema change, timer,
external I/O or new background work was introduced. Existing operation locks
serialize it with canonical Fueler saves.

### 2. Pre-ON unassignment retained a resolved unavailable-truck hold

The preparation-only unassignment branch skipped the hold-resolution helper
already used by the physical-visit branch. It now invokes that same helper
after clearing the removed truck. A still-ineligible Fueler keeps the hold;
permission and selection checks are retained. Preparation, history and
inventory are unchanged.

### 3. Optional row-builder invocation dereferenced an absent argument

`_fuel_rows()` resolved its optional assignment map, then used the original
possibly-None argument during direct-entry history lookup. It now uses the
resolved map. The original unknown-APU regression case passes without changing
fuel or APU calculations.

The first two failures were reproduced against functions loaded from the exact
starting Git blob, using the same new canonical fixtures: 2 failed, 6 passed.
The original module run independently reproduced the third defect. This is
isolated evidence, not production test fueling.

## Fixture corrections and preserved assertions

The four named cross-mission failures retain their original case IDs. They now
distinguish trustworthy Remaining provenance from fabricated Actual, transfer
and FOB readiness. A new canonical-source completion test independently proves
the carryover; synthetic legacy-event fixtures are not the sole evidence.

Cancellation fixtures now edit the fresh follow-up visit rather than corrupting
the completed parent's work. They use canonical ON/OFF/handoff where movement
is claimed, and query the intended audit action instead of assuming the entire
audit table has one row. Earlier immutable event snapshots, source mission/tail,
inventory, stale-cycle rejection, permission/CSRF checks and atomic rollback
assertions remain.

The schema test now admits a second same-tail visit, rejects duplicate visit
numbers and still checks duplicate tanks and negative Remaining/Actual.
Inventory tests now pass through the real five-stage lifecycle before testing
insufficient and unknown balances, rollback and repeated jobs. They continue
requiring real transfer events without fictitious balance restoration or
negative inventory. The no-truck FOB test uses actual readings and explicit
APU confirmation without inventing truck ON/OFF.

## Validation

All providers were mocked or blocked by the existing loopback-only test guard.
PostgreSQL used disposable private schemas on loopback PostgreSQL 17.11, never
Neon production. Existing dependencies were reused; no load/stress test ran.

* Focused Python: see the exact final summary in the evidence register. All
  six requested modules plus canonical physical evidence, lifecycle,
  continuation and submission tests were executed together. Remaining failures
  were retained rather than skipped or globally suppressed.
* PostgreSQL: 25 passing contract/lifecycle/submission cases, plus 4 passing
  inventory cases. These include actual simultaneous cancellation/save, a
  cancellation observed waiting on another mission's transaction in
  `pg_stat_activity`, duplicate protection, SAVEPOINT recovery, correction and
  handoff races, and event/inventory rollback.
* Chromium/WebKit: 2 passing test methods, each exercising both engines — mobile
  Fueler and desktop Dispatcher, real canonical MotherBrain A→B→A swaps,
  independent visit controls, handoff, corrections and completion.
* JavaScript: 18 passed, zero failed — lifecycle submission/draft guards,
  critical completion and Fueler Data behavior. No JavaScript changed.
* Changed Python files compiled; `git diff --check` passed.

Warnings were not suppressed. The existing SQLAlchemy/deprecation warnings
remain visible in local logs. No production performance or CU savings are
claimed from these fixtures.

## Remaining scope and production safeguards

The register identifies every still-failing selected ID. Outstanding-work and
older markup/status assertions require their own presentation/fixture contract
review. The trusted-lineage test still expects readiness from inherited
readings without the current verification preconditions. No uncertain
operational policy was changed merely to make those tests pass.

No production flights, tank readings, inventory, users, permissions, historical
records, Google Sheets or R2 objects were edited by this work. No model, FROST,
archive, employee-recovery, worker, hosting, security-header or CSRF policy
changed. No schema migration or historical data repair is required. Any
already-affected historical shared-tail projections require a separately
authorized, read-only comparison of their original audits and rollback records
before a repair could be considered.

## Exact results and changed files

Final combined focused run: **144 passed, 6 failed, 39 passing subtests** in
111.40 seconds, with 59,945 unsuppressed warnings. Of the original 38 failing
IDs selected from the R03 register, **32 pass and 6 remain; zero new failing
IDs**. No case was skipped or had a collection error. The 29 PostgreSQL cases,
18 JavaScript cases and 2 dual-engine browser methods above all pass.

The six remaining IDs (module/class followed by method):

* `test_neoscorpion_cancel_unassign.CancelUnassignTest::test_dispatch_visual_contract_reserves_fob_slot_through_live_refresh`
* `test_neoscorpion_cancel_unassign.CancelUnassignTest::test_ready_assignment_alert_threshold_refresh`
* `test_neoscorpion_fueling_status.TrustedFobInheritanceTest::test_explicit_trusted_lineage_uses_apu_adjusted_fuel_and_preserves_accounting`
* `test_neoscorpion_outstanding_work.OutstandingWorkTest::test_busy_truck_preparation_is_not_original_aircraft`
* `test_neoscorpion_outstanding_work.OutstandingWorkTest::test_genuine_earlier_tail_and_same_tail_physical_visits_keep_recovery`
* `test_neoscorpion_outstanding_work.OutstandingWorkTest::test_retained_same_tail_preparation_remains_reviewable_not_original`

Only these ten files are included:

* `app/services/neoscorpion.py`
* `tests/test_neoscorpion_cancel_unassign.py`
* `tests/test_neoscorpion_cycle_lines.py`
* `tests/test_neoscorpion_fuel_interruptions.py`
* `tests/test_neoscorpion_fueling_status.py`
* `tests/test_neoscorpion_tank_fuel.py`
* `tests/test_neoscorpion_physical_evidence.py`
* `tests/test_neoscorpion_physical_evidence_postgres.py`
* `docs/audits/2026-11-02-r03b-physical-fueling.md`
* `docs/audits/2026-11-02-r03b-physical-fueling-evidence.json`
