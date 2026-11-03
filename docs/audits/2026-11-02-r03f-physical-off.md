# R03F — four physical fueling OFF regressions

## Decision and source

**The four original cases pass with meaningful current-contract assertions.
No application defect was established; this release changes tests and audit
documentation only.** The wider R03 backlog remains open.

Fetched and safely synchronized main at
`419f9901a2e15eb4d8bf202123ee1565e1363a54`. Render independently reported
the same SHA LIVE in `dep-dbkmf67lk1mc73e1fpj0`. There were no tracked edits.
The six unrelated untracked artwork/prototype/documentation items and ignored
synthetic PostgreSQL directories were preserved. No applicable AGENTS.md was
found in the inspected repository instructions.

The R03E report/register, R03D completion report, October 16 lifecycle document,
current OFF/entry/submission/completion services, models, routes and browser
controls were inspected. The full three-parent baseline reproduced all four
scoped failures and four other failures: 8 failed and 25 passed.

## Verified boundaries

The five stages remain assignment → physical TRUCK ON → physical TRUCK OFF →
Fueler COMPLETE ASSIGNMENT → Dispatcher COMPLETE. Entry is preparation/data,
not physical ON. OFF records actual disconnection and releases the truck; it
does not claim final fuel verification or post a completed transfer.

For an assigned, genuinely started truck visit, final Remaining, Actual, APU
confirmation/source and T/F can be finalized after OFF. The submission service
then requires complete Remaining, complete Actual, confirmed valid APU evidence,
the correct physical visit and valid movement/T/F. Data entry with APU=yes still
rejects an invalid/missing source before persistence. Tests assert the specific
entry/submission errors rather than replacing them with generic failures.

The old APU/OFF test names are retained for original-ID accounting. Their
implication that final data must always block physical OFF is obsolete under
the October 16 contract. No application condition was weakened to satisfy them.
The old manually fabricated APU=yes/source=NULL state was replaced with actual
entry validation and a canonical not-confirmed state.

OFF legitimately marks stock unverified/pending while its transfer remains
unposted. Its numeric balance is not deducted. Fueler submission also does not
post an event or deduction. Dispatcher verification posts the exact recorded
transfer once. Explicit truckless FOB remains a separate no-transfer action;
no fake ON/OFF or transfer amount is manufactured from estimates.

## Four-case evidence

The [machine-readable evidence](2026-11-02-r03f-off-evidence.json) records the
original failures and final SQLite/PostgreSQL results for each original ID.

| Original method | First rejection and corrected fixture/expectation | Intended protection actually exercised |
| --- | --- | --- |
| `NeoScorpionFuelPlanningTest::test_apu_yes_without_source_blocks_off` | Missing physical truck preempted its old APU error; it also directly manufactured invalid APU ORM state. Now authenticates a Dispatcher assignment, confirms physical ON and uses real entry calls. | APU=yes/missing source rejects entry atomically with a precise source error. OFF with pending confirmation succeeds; final submission rejects the specific APU status/source requirement. A valid source permits submission without changing OFF/actor or creating events/deductions. |
| `NeoScorpionFuelPlanningTest::test_source_aware_fueler_workflow_preserves_source_through_off` | No assigned/started truck, and fixed revision expectations ignored the missing stages. | Canonical assignment/ON/readings/OFF retain CTR source, original confirmation time and 400-pound allowance, exact Work/visit/sequence/tail/truck and OFF actor/time. Revision deltas, duplicate OFF no-op, zero events and unchanged numeric stock are asserted. Original APU calculation inputs are retained. |
| `NeoScorpionFuelerOffTest::test_incomplete_actual_and_unconfirmed_apu_block_off` | Missing truck preempted obsolete Remaining/APU-before-OFF expectations. | A real physical visit disconnects with incomplete final data. Submission specifically rejects Remaining, then Actual, then APU confirmation as fields are completed. Valid final data submits while OFF/actor/permanent visit remain fixed; duplicate OFF is no-op and no event/deduction occurs. |
| `NeoScorpionFuelingEventFoundationTest::test_existing_save_and_off_create_no_events` | Positive T/F was set directly on an unassigned fixture before OFF. | Real assignment/ON plus explicitly entered measured tanks/T/F. Save, OFF and Fueler submission each retain zero events and 1,000 stock. Only Dispatcher completion creates the correctly owned event, preserves real ON/OFF and deducts 150 once to 850; repeat preserves immutable event fields and stock. |

The shared helper uses a real authenticated Dispatcher assignment route, followed
by the canonical physical ON API. It seeds only synthetic resources/selection
and mission records, never physical milestones, receipts, completion, transfer
events or deductions. Submission uses the exact current edit baseline.

Additional tests reject missing truck, absent physical ON, wrong Fueler,
nonexistent assignment, stale Work reference, cross-sort access and missing
CSRF. They compare work, assignment, stock, audit, revision and event snapshots.
An injected exception after OFF mutations begin rolls everything back; a
legitimate retry succeeds, and its duplicate preserves timestamp/actor once.

## PostgreSQL and browser verification

All four original cases and the new identity/rollback cases run on isolated
loopback PostgreSQL with production guards installed, asserting the actual engine.
Independent transactions prove duplicate OFF has one winner and one no-op.
Competing OFF/submission either submits valid evidence after real OFF or rejects
the premature/stale submission; submission cannot create OFF, post an event or
deduct stock. Prior correction/completion, physical-visit, R03B/R03C/R03D/R03E
PostgreSQL protections are included, preserving R03E's 409 source-parameter guard.

The new Chromium/WebKit workflow uses separate Fueler/Dispatcher accounts:
physical ON, partial Remaining only, physical OFF, disabled submission with the
precise missing-data reason, then final Actual/APU source/T/F entry after OFF.
Reload preserves the source and OFF. Changing the APU source invalidates the
receipt without reopening the clock. Fresh resubmission and Dispatcher
verification alone produce the correctly owned event/source snapshot and
150-gallon deduction. The test waits for the current submission version before
clicking COMPLETE, preserving the stale-request protection.

Fixture/harness issues were corrected without app changes: an APU-only update
initially sent the legacy form helper's blank T/F; it now sends only the actual
changed fields. Independent synthetic test clients receive separate app/request
globals instead of sharing an artificial outer Flask Login cache. The browser
waits for the fresh receipt version rather than an already-visible older button.

## Measured validation

Existing dependencies, synthetic databases and mocked/blocked providers were
used. No test was deleted, broadly skipped, expected-failure marked or stripped
of its integrity purpose. Warnings remain visible.

| Run | Actual result |
| --- | --- |
| Full three-parent starting baseline | 8 failed, 25 passed; 14 passing subtests; 11,584 warnings; 16.07 s |
| Four original IDs after initial fixture correction | 4 passed; 1,576 warnings; 3.88 s |
| Final four plus new identity/rollback cases | 6 passed; 2,368 warnings; 5.36 s |
| Complete parents plus prior repaired groups and new safety cases | 207 passed, 4 unchanged baseline failures; 65 passing subtests; 83,547 warnings; 148.61 s |
| Parent-only outcomes extracted from that full execution | 29 passed, 4 unchanged baseline failures |
| Previously repaired R03B/C/D/E group extracted from that execution | All 176 passed |
| PostgreSQL with production guards | 69 passed; 11 passing subtests; 30,030 warnings; 225.95 s |
| New Chromium/WebKit OFF/APU workflow | 1 method, both engines passed; 762 warnings; 33.32 s |
| Prior Chromium/WebKit correction, visit and dirty-draft aggregate | 3 passed, 1 browser harness failure; 3,854 warnings; 197.14 s |
| Correction browser after redirect synchronization repair | 1 method, both engines passed; 878 warnings; 49.99 s |
| JavaScript lifecycle/completion/Fueler Data tests | 18 passed, zero failed/skipped |
| Compilation, JS syntax, whitespace | All eight changed Python test modules compile; both affected JS files parse; `git diff --check` passes |

AST comparison confirms every original parent method remains, and all unrelated
method bodies/helpers are unchanged. The four unrelated parent failures are
retained visibly, not silently omitted from the combined suite:

| Unchanged original ID | Reproduced first diagnostic; outside this correction scope |
| --- | --- |
| `test_hanzo_fueling_identity_timing_and_required_warning` | Expected `02:15`; current displayed timing is `—`. Canonical timing provenance needs a separate investigation. |
| `test_get_never_marks_off_and_ready_card_offers_action` | Existing TRUCK OFF control markup conflicts with its old absence assertion. Its complete state/eligibility contract is not reclassified here. |
| `test_physical_off_does_not_require_final_tf_and_repeat_is_noop` | Expected retired `<dt>OFF</dt>` markup. Its original body is unchanged; this report does not silently fix a selector outside the four-case scope. |
| `test_fuel_on_board_without_transfer_creates_no_events` | Canonical FOB rejects insufficient verified fuel. Its fixture/target/readiness contract needs separate verification. |

These remain failures in the original inventory. None is declared a confirmed
production defect or harmless obsolete test solely from its first diagnostic.

The prior correction browser test initially accessed a child control before its
native POST's final redirected page had loaded. It now waits for that navigation
through DOMContentLoaded around the existing correction/reopen clicks. Its
assertions, response checks, real UI actions and timeouts are unchanged; there
is no forced click, ignored failure or mutation replay. The corrected workflow
passes both engines on its separate final rerun. Together with the other three
unchanged browser methods and the new OFF method, five distinct browser methods
have final passing coverage across Chromium/WebKit. The initial failure remains
in the evidence ledger. No application regression was established by that race.

## Original inventory and remaining risk

All 367 original IDs are rerun under the established sequential functional-test
harness: 2 GiB owned-process-tree limit, 4 GiB workstation headroom, 20-minute
deadline and blocked external provider access. This is not a production stress
test. The original R03A rename remains explicitly mapped.

The measured final result is **67 passing / 300 failing unique original IDs**,
against the R03E baseline of **63 passing / 304 failing**. Exactly these four
newly pass; no previously passing original ID regressed. All 367 IDs executed,
with no skips, missing IDs or collection errors. Pytest reported 589 failure
events, 99 passing outcomes and 356 passing subtests in 252.09 seconds; these
are not 589 unique broken methods or 99 wholly passing original IDs. The owned
test tree peaked at 905,170,944 bytes (863.2 MiB); no resource limit was reached. The
[updated backlog register](2026-11-02-r03f-backlog-register.json) preserves every
original ID, prior classifications/provenance and current diagnostics.
Subtest/failure-event totals are reported separately from unique failing IDs.

The next coherent scope is the remaining completion-handoff prerequisite pair:
`DispatchReactivationTest::test_valid_followup_returns_completed_mission_to_unfinished`
and `TruckDriverTest::test_current_upcoming_off_and_completed_readings_are_canonical`.
Both currently stop at missing Fueler COMPLETE ASSIGNMENT. Rebuild real submitted
and Dispatcher-verified parents, then examine follow-up state and authoritative
truck readings. Do not assume their later assertions are correct or harmless.
Other unresolved authority, mission identity, staffing, presentation and query
clusters remain separate. No full-system green or final audit sign-off is claimed.

## Exact files and production boundary

Only these eleven files are included:

* `tests/test_neoscorpion_fuel_planning.py`
* `tests/test_neoscorpion_fueler_off.py`
* `tests/test_neoscorpion_fueling_event_foundation.py`
* `tests/neoscorpion_off_workflow.py`
* `tests/test_neoscorpion_off_contract.py`
* `tests/test_neoscorpion_off_contract_postgres.py`
* `tests/browser/test_neoscorpion_off_boundary.py`
* `tests/browser/test_neoscorpion_corrections.py` — native redirect waits only
* `docs/audits/2026-11-02-r03f-physical-off.md`
* `docs/audits/2026-11-02-r03f-off-evidence.json`
* `docs/audits/2026-11-02-r03f-backlog-register.json`

No application, model, schema, permission, calculation, asset, worker, provider,
archive format or configuration change is included. Existing R03E app code is
unchanged. The release is the commit containing this report; its final SHA,
Render AutoDeploy ID and read-only production checks are recorded after push
under ignored `instance/r03f` and supplied in the completion response.

The disposable PostgreSQL server is stopped and its files retained. No
policy-blocked temporary-directory deletion is attempted. No production fuel,
event, inventory, aircraft, employee, permission, Google Sheet, R2/archive or
FROST-evidence test write or historical repair is performed. Unrelated work,
previous reports and hosting settings remain intact.
