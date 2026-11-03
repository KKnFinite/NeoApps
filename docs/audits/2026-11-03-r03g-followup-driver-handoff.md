# R03G — follow-up reactivation and Truck Driver evidence

## Decision and starting evidence

**Both scoped original cases pass through the genuine Fueler-to-Dispatcher handoff. No NeoScorpion application defect was established. This release changes tests and audit documentation only; the wider R03 backlog remains open.**

Fetched and safely synchronized main at `4f5357f3ccb0e25caf2612985dfc5037fff6a1db`, rather than assuming the older requested reference. Render independently reported that starting SHA LIVE in `dep-dbkrdpavcj2c739qo09g`. There were no tracked edits. Six unrelated untracked artwork, NeoRain prototype and documentation items, and all ignored synthetic PostgreSQL directories, were preserved. No applicable AGENTS.md was found in the inspected repository/parent paths.

Reviewed the R03F report/register, R03D/E completion/correction reports, October 16 lifecycle contract, physical Work/event/tank ownership, follow-up services, Driver projection and relevant permissions/routes. Both unchanged original cases reproduced exactly `Fueler COMPLETE ASSIGNMENT required before dispatcher COMPLETE.` Adding a real current-baseline submission reached their downstream assertions successfully. The missing prerequisite was an invalid fixture, not a reason to weaken the submission guard.

## Canonical workflow and per-case results

The scoped helper seeds only synthetic planning/resources, commits those records, authenticates a Dispatcher through the real assignment route, and calls canonical physical ON. It never sets ON/OFF, receipts, completion, events or inventory transactions using ORM assignments. A controlled assignment clock precedes the real ON call. Readings/APU/T/F, physical OFF, committed Fueler submission and Dispatcher verification use their current services. Fresh HTTP app contexts preserve independent actor/request state. The original shared timeline helper is not changed.

| Original case | Meaningful assertions now exercised | Final result |
| --- | --- | --- |
| `tests/test_neoscorpion_dispatch_order.py::DispatchReactivationTest::test_valid_followup_returns_completed_mission_to_unfinished` | Real readings/OFF/submission precede verification. One 3,000-gallon event/deduction changes 10,000 stock to 7,000. Duplicate verification is a no-op. Follow-up creates cycle 2 and a new permanent Work ID, retaining the finalized original Work, event/tank snapshots, mission/tail and stock. Only prior Actual becomes new Remaining; new Actual/APU/ON/OFF/submission/T/F are not fabricated. Existing completed-versus-active ordering and actual-arrival priority assertions remain. | SQLite and PostgreSQL pass |
| `tests/test_neoscorpion_truck_driver.py::TruckDriverTest::test_current_upcoming_off_and_completed_readings_are_canonical` | Authorized claim and canonical planning retain established upcoming order. Real ON is current; physical OFF moves the same Work to previous while administrative completion is still false. Remaining 6,000/0/6,000 and Actual 15,000/0/15,000 belong to that visit; measured zero is visibly distinct from missing. Submission posts nothing. Verification changes administrative state once, preserving OFF actor/time, permanent visit, readings, event ownership and 7,000 stock. | SQLite and PostgreSQL pass |

AST comparison retains every original parent method ID. All unrelated method bodies remain identical; only the two original test bodies and the scoped Driver preparation delegate change. There are no deletions, broad skips, expected failures or weakened eligibility assertions.

## Additional integrity/security coverage

* Real routes reject unauthorized Fuelers, stale cycle/tail, foreign-sort assignment and missing CSRF without changing closed-parent evidence. A successful follow-up cannot be replayed with its stale parent cycle.
* An injected exception after fresh Work allocation/flush rolls back assignment, mission, tank, cycle-history, tail-fuel, audit and inventory state. Retry creates exactly one valid new cycle, preserving the old event.
* Driver context and authenticated page/revision/panel GETs emit zero NeoScorpion INSERT/UPDATE/DELETE statements and preserve persisted snapshots. Closed Actual remains separate from a fresh upcoming visit's inherited Remaining and NULL Actual.
* Independent PostgreSQL transactions prove submission must precede verification, duplicate verification posts one event/deduction, and simultaneous follow-up starts allocate one new cycle while rejecting the stale competitor. Production schema/immutability guards are installed in disposable UUID schemas.
* Existing R03E current-Work/source-parameter HTTP 409 tests remain in the passing prior regression coverage. No route status, permission, source authority, lifecycle, calculation or transaction guard was changed.

Initial PostgreSQL fixture failures were investigated rather than bypassed: pending planning-row FK locks blocked a second client's operation lock; setup read transactions blocked bootstrap DDL; independent sessions invalidated detached actor objects. The test helper now commits planning records before HTTP, releases setup transactions before bootstrap and reloads canonical IDs after concurrent sessions. One identified blocked query in the local synthetic database was canceled so that failed test could release its locks. Production databases and unrelated processes were never targeted. The local server was stopped and its directory retained.

## Validation and limitations

| Execution | Result |
| --- | --- |
| Two unchanged original IDs | 2 failed at missing Fueler handoff |
| Two IDs after initial real submission | 2 passed |
| Final two IDs plus additional security/rollback/Driver contracts | 5 passed, 3 passing subtests |
| Final Driver module plus follow-up and contracts | 11 passed, 8 passing subtests |
| Prior R03B/C/D/E/F coverage, complete parents and continuation regression run | 224 passed, 5 unchanged baseline failures, 73 passing subtests |
| Previously passing methods from the R03F selected run | All 207 remain passing, including the previously repaired B/C/D/E/F groups |
| PostgreSQL final unique coverage | 80 passing methods: prior 72 plus all 8 new scoped cases; original aggregate fixture errors and the final scoped rerun are both retained in evidence |
| New browser workflow | One method passes both Chromium and WebKit with separate authenticated Dispatcher, Fueler and Truck Driver contexts, mobile and desktop |
| JavaScript | 22 passed, zero failures/skips |
| Compilation/syntax/whitespace | All six changed/new Python files compile; affected shared JS parses; `git diff --check` passes |

The five unchanged Python regression failures are the four documented R03F cases (Hanzo timing, two physical-OFF markup/eligibility cases, truckless FOB sufficiency) plus `DispatchOrderTest::test_only_final_presentation_copies_rows_not_projection_or_plan`. That ordering mock supplies a bare `object()` as `truck_visuals`, which current presentation iterates. The latter failure was independently reproduced from the unchanged starting test source. These remain visible; no application rule was altered to make them pass.

Two unchanged older browser methods also remain failing. The correction/reopen method first reached final event/inventory assertions but failed its final WebKit localhost-fetch console check; a separate unchanged rerun failed a `.one()` audit-count assumption. The physical-visit method failed its receipt assertion with Actual readings still missing and an old `SUBMITTED` expectation. Their autosave/receipt/audit harnesses need independent review; this package does not declare them harmless or force them green. The new targeted browser workflow waits for actual autosave acknowledgment/current receipt version and successfully exercises assignment, ON, measured readings, OFF, submission, verification, immutable Driver history and fresh follow-up through the actual UI in both engines.

Existing datetime/ORM warnings are retained. Tests use synthetic SQLite/loopback PostgreSQL data and mocked/blocked providers. No full-system pass, production CU saving or broad browser sign-off is claimed.

## Measured original 367-ID inventory

The complete original inventory is rerun sequentially after the final fixture fixes under the established 2 GiB owned-process-tree cap, 4 GiB workstation-headroom guard and 20-minute deadline. No production stress/load test is used. Every original ID remains accounted for, including the established R03A rename.

The measured result is **68 passing / 299 failing unique original IDs**, compared with R03F's 67/300. Both scoped IDs newly pass. One previously passing asset-metadata ID already fails at the starting release, accounting for the net change of one rather than two. Unique failing IDs and pytest failure/subtest events are recorded separately in the [machine-readable register](2026-11-03-r03g-backlog-register.json) and [execution evidence](2026-11-03-r03g-handoff-evidence.json).

The pre-existing asset failure is confirmed, not speculative: `25-neosubzero.css` contains 18,771 bytes while `sequence.json` still declares 19,118. Both application and PageAssets test sources exactly match the starting SHA; R03G changes no application files. This is delivery-metadata drift from the earlier palette release, not a new fueling failure. It remains a separate, small metadata correction; no application assets or manifest are edited in this two-case package.

## Remaining priority and next scope

The original completion-handoff prerequisite pair is now closed. The next coherent high-risk NeoScorpion scope is **truckless FOB and trustworthy legacy physical-ON evidence**, beginning with `test_fuel_on_board_without_transfer_creates_no_events`, `test_identified_truckless_fob_verification_is_preserved`, `test_legacy_legitimate_and_completed_timestamps_are_not_rewritten`, and `test_uncertain_legacy_readings_never_slide_a_legitimate_on_forward`. Verify actual evidence/target/readiness contracts before revising old assertions. The two retained source-visit/correction browser failures merit a separate autosave/receipt/audit investigation. Static manifest metadata is a small independent correction. Other mission/sort, authorization, staffing, Google authority and query-budget cases remain open with prior provenance retained; counts alone do not establish production defects.

## Exact files and release safety

Only these nine files are included:

* `tests/test_neoscorpion_dispatch_order.py`
* `tests/test_neoscorpion_truck_driver.py`
* `tests/neoscorpion_reactivation_workflow.py`
* `tests/test_neoscorpion_reactivation_contract.py`
* `tests/test_neoscorpion_reactivation_postgres.py`
* `tests/browser/test_neoscorpion_reactivation.py`
* `docs/audits/2026-11-03-r03g-followup-driver-handoff.md`
* `docs/audits/2026-11-03-r03g-handoff-evidence.json`
* `docs/audits/2026-11-03-r03g-backlog-register.json`

No application code, schema, configuration, security policy, operational calculation, permission, archived format, FROST model or unrelated asset change is included. No real production fuel transfer, completion, inventory, follow-up, employee, Google or R2 mutation or historical repair is performed. No blocked temporary-directory cleanup is retried. The test/documentation release uses main and existing Render AutoDeploy; no manual deployment/configuration change is made. Exact final SHA/deploy identity, bootstrap/worker startup, read-only health/login and authenticated Fueler/Dispatch/Driver checks are recorded after push under ignored `instance/r03g` and in the completion response. Dispatch is opened only after checking that its browser-triggered automatic actions are disabled.
