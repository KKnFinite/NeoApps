# R03E — seven legacy Fuel Correction regressions

## Decision and source

**The seven original cases pass with canonical workflows. One related
current-work integrity defect was independently reproduced and corrected.**

Fetched and safely synchronized main at
`8ee774d60a0497c136e799d6f7c2fbd728bfc6d3`. Render independently reported
the same SHA LIVE in `dep-dbklnr67bikc73948eqg`. There were no tracked edits;
the six unrelated untracked artwork/prototype/documentation items were preserved.
Ignored synthetic PostgreSQL files remain intact. No applicable AGENTS.md was
found in the inspected repository instructions.

The R03D report and full backlog register, R03B physical-fueling report,
R03C contract report, October 16 lifecycle documentation, current services,
routes, templates, submission/audit models and passing regression tests were
inspected. All seven original failures reproduced on the starting source at
`Assign the truck used for positive T/F before OFF.` No original downstream
assertion was accepted without reaching it through valid work.

## Authority and lifecycle contract

The replacement `_ready_off()` creates a synthetic nightly truck/Fueler selection,
assigns the truck with `save_dispatch_assignment()`, confirms physical ON,
records Remaining/Actual/APU/T/F through `save_fueler_entry()`, and records
physical OFF. It does not fabricate ON/OFF, submission, completion, events or
deductions with ORM assignments. Tests add a real Fueler submission only when
receipt validity or Dispatcher completion is relevant.

**Physical OFF is not a blanket data-entry freeze.** The October 16 contract,
current Fueler service/template and existing submission tests explicitly permit
final readings after OFF. Those saves preserve physical OFF and cannot reopen
the physical clock. Material changes revoke the current submission by its
version/fingerprint, preserving its historical actor/time/evidence. Dispatcher
completion still requires a current valid Fueler receipt.

Explicit Dispatcher REOPEN OFF requires a reason when OFF is present. It audits
the old OFF timestamp and actor, invalidates receipt validity and clears OFF on
the same permanent work/visit/sequence. Repeating it while already open is a
no-op. A subsequent physical OFF records a new timestamp on that visit.
Completed assignments and explicit truckless FOB reject reopen/correction.
Finalized event snapshots and stock cannot be rewritten through these actions.

The old freeze test retains its original ID for backlog accounting, but now
protects the actual boundary: post-OFF final-data editing remains permitted;
after genuine Dispatcher completion, changed Fueler saves fail, the active
Fueler endpoint closes and the authorized historical panel is read-only.
No established operational rule was changed to satisfy its obsolete name.

## Independently confirmed application defect

`_locked_current_fuel_assignment()` skipped the current Work reference check
when a request supplied `physical_work_id`, because supported source-visit
actions resolve their exact owner separately. The legacy CORRECT ACTUAL and
REOPEN OFF actions did not resolve a source owner; they still mutated current
work. Supplying a source parameter therefore bypassed their stale-work guard.

An isolated canonical reproduction showed:

* Valid assigned truck, real ON/readings/OFF; Actual Left 11,000 and revision 4.
* Authorized correction with a mismatched `expected_work_ref` plus a
  `physical_work_id` returned 302, changed Actual Left to 12,000, appended its
  audit and advanced revision to 5.
* The equivalent reopen request returned 302, cleared OFF, appended its audit
  and advanced revision to 6.

This is an operational integrity defect for an authorized Dispatcher request,
not a permission escalation or evidence of affected production records.
The original seven cases did not exercise this source-parameter path.

### Narrow correction

The two legacy actions now explicitly select `current_work_only=True` in the
existing locked helper. They reject source-visit parameters before mutations;
ordinary stale references continue to reject under the existing reference check.
Supported source data/submission/completion paths retain their existing owner
resolution. No source history is redirected, deleted or reclassified.

The two routes now return HTTP 409 for `FuelerDataConflict`, retaining the
existing HTML/flash error contract. Ordinary validation failures remain 400 and
permission denials remain 403. Rollback precedes error rendering. No mutation
is retried automatically. Supported current forms and source-operation APIs
retain their existing inputs and permissions.

No query, schema, worker, timer, provider, archive format or inventory-calculation
change is introduced. Legacy trusted service calls without a browser reference
retain their existing current-assignment semantics; this is not a redesign of
per-field optimistic concurrency or historical correction policy.

## Seven-case evidence

The [machine-readable evidence](2026-11-02-r03e-correction-evidence.json) retains
every original ID, first failure, contract/correction and final SQLite/PG result.

| Original method | Root cause after the invalid physical fixture | Meaningful final assertions |
| --- | --- | --- |
| `test_reopen_requires_reason_and_audits_prior_off` | Assumed the entire audit table was empty, ignoring legitimate ON/data audits. | Missing reason preserves baseline audits/revision/OFF. Valid reopen records exact operation/assignment/work, prior OFF and actor, reason and Dispatcher. Same visit remains, no event/deduction; duplicate reopen is no-op. |
| `test_reopened_work_can_be_edited_and_marked_off_again` | Global `query.one()` assumed only one audit existed. | Selects the exact reopen audit; original OFF remains recorded. Edit and new OFF preserve permanent Work/visit/sequence/ON identity and actor, with unchanged stock and no event. |
| `test_completed_assignments_cannot_reopen_or_be_corrected` | Completion/FOB was manufactured by directly setting/clearing completion fields. | Real handoff and Dispatcher completion produce an immutable event and one 150-gallon deduction. Reopen/correction reject without changes. A separate genuine no-truck FOB also rejects both, preserving the original event. |
| `test_fueler_save_is_frozen_after_off_and_ui_is_read_only` | Obsolete expectation that physical OFF freezes final-data editing. | Post-OFF edits preserve OFF and revoke receipt validity. After real Dispatcher completion, edits reject and historical UI is read-only. No ordinary save reopens physical OFF. |
| `test_actual_correction_audits_each_change_once_and_preserves_off` | Counted every audit row as an Actual correction. | Exact two changed tank deltas, prior/new pounds, actor/reason/scope; OFF and real T/F retained; total recomputed. Identical correction produces zero new audit/revision. No event or stock deduction. |
| `test_clearing_actual_makes_completion_incomplete_without_clearing_off` | Initial fixture blocked the intended clearing assertions. | Real submitted work loses receipt validity/readiness when Actual is cleared; historical receipt time remains. Total is unavailable, OFF remains, completion rejects missing Actual, no event/deduction. |
| `test_dispatcher_routes_commit_each_meaningful_action_once` | Initial fixture blocked both route assertions. | Authorized correction/reopen each commit once. Identical correction and repeated reopen commit zero times and append no duplicate audit. No physical event or deduction occurs. |

The existing no-op correction and fuel-entry delta-audit tests remain intact.
New tests cover stale-work/source-parameter failures, Fueler permission denial,
CSRF rejection, stale Fueler edit baselines and injected correction/reopen
transaction failures. The failure injection occurs after the actual mutations
have begun; rollback restores tanks, OFF, receipt version, audits and revision,
then a legitimate retry succeeds without event/inventory effects.

## Validation

Existing dependencies and isolated synthetic SQLite/loopback PostgreSQL were
used. External provider access was blocked. PostgreSQL correction tests install
the production guards, including the audited OFF-reset constraint, and assert
the actual PostgreSQL engine. The SQLite-only DDL bootstrap method remains in
the SQLite suite; all other correction methods also run on PostgreSQL.

| Run | Measured result |
| --- | --- |
| Starting correction module | 7 failed, 3 passed; 3,664 warnings; 6.85 s |
| Only physical setup corrected | 4 failed, 6 passed; 3,854 warnings; 6.89 s |
| Original module with verified expectations, before new safety cases | 10 passed; 3,980 warnings; 7.26 s |
| Added safety cases before application correction | Four failure events across two new IDs: source-parameter mutation returned 302; ordinary stale conflict returned 400 |
| Final complete correction module | 14 passed; 6 passing subtests; 5,623 warnings; 10.81 s |
| R03B/R03C/R03D and related lifecycle/submission/continuation/physical evidence | 176 passed; 51 passing subtests; 71,023 warnings; 131.22 s |
| PostgreSQL correction plus completion/physical-evidence/lifecycle coverage | 61 passed; 11 passing subtests; 26,859 warnings; 199.02 s |
| Final stricter PostgreSQL race assertions | 2 passed; 820 warnings; 7.96 s; reruns within the 61 distinct methods |
| New Chromium/WebKit correction/reopen/completion workflow | 1 method, both engines passed; 859 warnings; 39.59 s |
| Existing Chromium/WebKit visit and two-account dirty-row/open-dialog workflows | 3 methods, both engines passed; 3,328 warnings; 145.45 s |
| JavaScript lifecycle/completion/Fueler Data tests | 18 passed, zero failed/skipped |
| Compilation, JavaScript syntax, whitespace | Five changed Python modules compile; both affected JS sources parse; `git diff --check` passes |

The new browser workflow uses real separate-account controls, corrected Actual
and audit values, same-visit audited reopening, a later physical OFF, fresh
handoff, Dispatcher verification and exact event/stock identity. It checks console
errors and absence of completed Actual correction controls. Initial new harness
issues were fixed: detached fixture user references became scalar IDs; OFF waits
now observe the committed response and match URL paths including visit query
parameters, rather than matching the already-present OFF button label.
No application change was justified by those harness failures.

Existing SQLAlchemy/datetime warnings remain visible; they were not suppressed.
No test was deleted, broadly skipped, expected-failure marked or stripped of its
integrity purpose. These results are scoped verification, not full-system sign-off.

## Original R03 inventory and remaining risks

The original 367-ID inventory is executed with the established sequential
functional-test harness: 2 GiB owned-process-tree limit, 4 GiB system headroom,
20-minute deadline and blocked external providers. No production load test is
performed. The original R03A rename remains explicitly mapped.

The measured final inventory is **63 passing / 304 failing original IDs**,
against the **56 passing / 311 failing** R03D baseline. Exactly the seven scoped
IDs newly pass; no previously passing ID regressed. All 367 IDs executed, with
no skips, missing IDs or collection errors. Pytest reported 593 failure events,
95 passing outcomes and 356 passing subtests in 246.99 seconds. These are not
593 unique broken methods or 95 wholly passing original IDs. The monitored
owned test tree peaked at 902,209,536 bytes (860.4 MiB); no resource limit was
reached. The updated
[backlog register](2026-11-02-r03e-backlog-register.json) preserves every original
ID, earlier classifications/provenance, current diagnostics and R03D comparison.
Pytest subtest/failure-event totals are distinguished from unique failing IDs.

Remaining failures are unresolved verification gaps, not automatically proven
production defects or obsolete assertions. The next coherent scope should be the
remaining physical-start/OFF prerequisite cluster, independently verifying the
current OFF contract and keeping mission/visit, inventory and handoff protections.
The closest four IDs are the two source/APU OFF cases in
`test_neoscorpion_fuel_planning.py`,
`test_incomplete_actual_and_unconfirmed_apu_block_off` in
`test_neoscorpion_fueler_off.py`, and
`test_existing_save_and_off_create_no_events` in
`test_neoscorpion_fueling_event_foundation.py`. Two additional prerequisite
failures concern follow-up reactivation and Truck Driver handoff; they remain
separate. These are investigation priorities, not assumed application defects.
Other mission identity, Google authority, staffing permission, query-budget and
presentation clusters remain separate; no broader R03 green claim is made.

## Changed files and production boundary

Only these eight files are included:

* `app/services/neoscorpion.py`
* `app/neonodes/neoscorpion/routes.py`
* `tests/test_neoscorpion_fuel_corrections.py`
* `tests/test_neoscorpion_fuel_corrections_postgres.py`
* `tests/browser/test_neoscorpion_corrections.py`
* `docs/audits/2026-11-02-r03e-fuel-corrections.md`
* `docs/audits/2026-11-02-r03e-correction-evidence.json`
* `docs/audits/2026-11-02-r03e-backlog-register.json`

The release is the commit containing this report. Final commit/deployment IDs
and read-only rollout results are recorded after push under ignored
`instance/r03e` and supplied in the completion response. Existing Render
AutoDeploy is used; no manual deployment or configuration change is requested.
The disposable PostgreSQL server is stopped, its files are retained, and no
policy-blocked temporary-directory deletion is attempted.

No production correction, reopen, completion, fuel reading, truck inventory,
mission/employee/permission, Google Sheet, R2/archive, FROST evidence or historical
repair test write is performed. Unrelated work and previous reports remain intact.
