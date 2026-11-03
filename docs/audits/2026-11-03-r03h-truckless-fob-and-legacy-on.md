# R03H — truckless FOB and trustworthy historical ON

## Decision and baseline

The four scoped cases require test repairs, not a change to operational fueling. No new application defect was demonstrated. Saved preparation cannot manufacture TRUCK ON, and 30,000 lb of measured fuel cannot satisfy a 50,000 lb required target. The current separate, authorized no-transfer FOB workflow is preserved.

Fetched and safely synchronized main at `787c7651c9b9a5f2008a36cdf7ca4e75910f6751`. Render independently reported the same starting SHA LIVE in `dep-dbl34sflk1mc73ejn6d0`. There were no tracked edits; the six unrelated untracked artwork/prototype/documentation paths and ignored synthetic PostgreSQL files were retained. No applicable AGENTS.md was found in the inspected repository/parent paths.

Reviewed R03G's report/register, R03F/E lifecycle and correction evidence, October 16 lifecycle documentation, the canonical FOB/save/ON/OFF/submission/completion services, physical-start provenance, permanent Work/tank relationships and route authorization. All four original failures reproduced on the unchanged starting release. No historical production repair was attempted.

## Verified contract and per-case corrections

| Original method | Root cause | Meaningful final coverage |
| --- | --- | --- |
| `test_fuel_on_board_without_transfer_creates_no_events` | Invalid fixture: its three Actual readings totaled 30,000 lb, below the 50,000 lb required load. | Insufficient verification is rejected without completion. Canonical readings are then saved at exactly 50,000 lb with APU confirmed off. Dispatcher FOB changes one revision, records its actor and completes the same Work with zero transfer. Duplicate verification is a no-op; no truck/ON/OFF/event is created. |
| `test_identified_truckless_fob_verification_is_preserved` | Obsolete assertion that a saved reading produces `on_at_utc` and FUELING. | Canonical complete Actual and APU confirmation establish readiness, independently of preparation. The real authorized Dispatcher FOB route produces FOB status and recorded authority, with the same permanent Work and no fabricated physical timestamps or event. |
| `test_legacy_legitimate_and_completed_timestamps_are_not_rewritten` | Incomplete historical fixture assumed that tank creation creates ON, then directly assigned OFF/completion fields. | An explicitly labeled historical raw ON has contemporaneous complete readings and valid assigned-resource provenance. It is preserved as data changes. New manual confirmation, OFF, Fueler submission and Dispatcher completion use canonical services. Original raw ON, actual manual ON and actual OFF remain distinct and unchanged. No new completion is fabricated through ORM assignments. |
| `test_uncertain_legacy_readings_never_slide_a_legitimate_on_forward` | Outdated FUELING expectation for uncertain preparation with no recorded ON. | Preparation remains ASSIGNED with no active/trusted ON or performance estimate. Completing the readings still creates no ON. A later genuine physical confirmation establishes the effective ON; later saves preserve it and do not fabricate OFF/submission/completion. |

### FOB authority and evidence

The no-truck FOB service locks the current operation/assignment and exact current Work. It requires no assigned truck, no positive transfer and no physical ON/OFF/transfer-review evidence. Ordinary verification requires complete Actual, explicit valid APU state/source and net verified fuel at least equal to the current required load. Remaining, inbound estimates and unknown APU cannot substitute for verified Actual. The separate existing trusted same-tail inheritance contract is not changed. DEFUEL cannot use this shortcut.

Additional tests cover greater-than-target Actual, duplicate verification, unknown APU, insufficient Actual despite ample Remaining, foreign mission/sort, Fueler permission denial, missing CSRF, stale Work baseline and failure after finalization has flushed. Caller rollback restores assignment/mission/Work/tanks/audits/inventory state; retry succeeds once. Read-only Fueler/row contexts produce zero NeoScorpion INSERT/UPDATE/DELETE statements. No FOB test creates an inventory audit or transfer event.

### Historical ON trust

Timestamp existence and trustworthy physical timing are separate. Current saves record preparation only. Manual ON must match its truck/cycle. Persisted historical automatic confirmation is accepted within its provenance rules and is not moved by later tank updates. A raw legacy ON is accepted only with appropriate resource timing, contemporaneous complete Remaining and the historical resource gate. Tank creation timestamps alone create nothing. Uncertain raw ON is retained in REVIEW without trusted/active physical timing or a performance estimate; no historical timestamp is repaired or slid forward. Existing performance-context keys remain intact when legitimate participation metadata is added.

Controlled ORM timestamp fixtures are labeled historical compatibility evidence only. New ON/OFF/submission/completion actions use canonical services. Completed-event immutability and exactly-once inventory remain covered by the prior lifecycle/physical-visit regression groups; this package changes neither implementation nor schema.

## PostgreSQL and browser evidence

Disposable loopback PostgreSQL UUID schemas install production schema/immutability guards. Independent requests prove duplicate FOB changes exactly once. Competing Actual reduction and FOB verification serialize: either sufficient 30,000 lb is verified and the later completed-work edit is rejected, or the reduction to 20,000 lb commits and verification is rejected as insufficient. No outcome verifies insufficient fuel, posts a transfer event or deducts inventory.

An initial foundation-test wrapper omitted that fixture's explicit teardown, leaving its read transaction open before schema cleanup. Only the identified local test cleanup query was canceled; the wrapper now tears down its owned fixture before schema cleanup. The final scoped PostgreSQL rerun passes. No production connection/query, unrelated process or temporary-directory deletion was targeted. The local PostgreSQL server is stopped after testing and its files retained.

The new browser method passes in both Chromium and WebKit with separate authenticated Dispatcher/Fueler contexts at desktop 1440 px and mobile 390 px. Actual UI assignment, autosaved complete measurements, absence of physical ON and the Dispatcher FOB button are exercised with CSRF and CSP enabled. Database assertions prove the same Work completes without ON/OFF/event/inventory. The prior real five-stage handoff/follow-up/Driver browser workflow also remains in regression coverage. Local screenshots are retained under `instance/r03h/browser`.

## Validation and original inventory

Final measured counts, execution times, retained previous passes and unique-versus-subtest failure events are recorded in [execution evidence](2026-11-03-r03h-fob-evidence.json) and the [367-ID backlog register](2026-11-03-r03h-backlog-register.json). The complete original inventory is executed after final changes, sequentially under the established 2 GiB test-process-tree cap, 4 GiB workstation-headroom guard and 20-minute deadline. No production load/stress test is used. No test is deleted, broadly skipped, marked expected-failure or weakened to manufacture a pass.

| Execution | Measured result |
| --- | --- |
| Four original IDs, unchanged baseline | 4 failed at the reported fuel-target/ON assumptions |
| Four repaired IDs plus eight additional evidence/security/rollback contracts | 12 passed |
| Complete two parent modules and new contract module, within final regression run | 27 passed / 4 unchanged failures |
| Prior R03B/C/D/E/F/G coverage and affected complete parents | 243 passed / 8 baseline failures; 73 passing subtests, 187.54 s |
| Previously passing R03G selected methods | All 224 remain passing |
| Scoped PostgreSQL final run | 14 passed, 38.89 s; includes all four original IDs and two independent-transaction races |
| Prior PostgreSQL regression run | 80 passed; 11 passing subtests, 246.86 s |
| New actual-UI FOB browser method | Passes Chromium and WebKit, 25.53 s |
| Prior actual-UI five-stage/follow-up/Driver browser method | Passes Chromium and WebKit, 58.30 s |
| JavaScript | 22 passed, zero failures/skips; Fueler data, compact completion and Dispatch workflow tests |
| Compilation/syntax/diff | Five changed/new Python files compile, shared Fueler/Dispatch JS syntax checks and `git diff --check` pass |
| Complete original R03 inventory | **72 passed / 295 failed unique original IDs**, 252.49 s; all 367 accounted for, no skips/collection errors |

The original inventory improves from R03G's measured 68/299 by exactly these four IDs. **No previously passing original ID regresses.** Raw pytest outcomes are 584 failure events, 105 passing outcomes and 355 passing subtests; these are not unique-ID totals. Peak monitored owned-process memory was 905,412,608 bytes (about 863 MiB), below the 2 GiB cap. No production performance saving is inferred from this synthetic test run.

The eight retained Python failures are Hanzo timing; two physical-OFF markup/eligibility assertions; the Dispatch presentation mock supplying non-iterable `object()` truck visuals; and four other physical-start/travel assertions (`test_legacy_premature_on_preserved_but_never_used_for_overdue_estimate`, `test_manual_on_overrides_inferred_on_and_predicted_travel_not_busy_truck`, `test_partial_or_truckless_non_fob_does_not_start_and_complete_valid_save_does`, `test_preassigned_truck_release_travel_and_expiry_never_auto_create_on`). Their sources are unchanged, and the broader original inventory retains their failure evidence. No guard is relaxed to bypass them. Initial exploratory contract/fixture failures are retained separately from final passing executions in the evidence JSON.

The two older correction/visit browser failures documented in R03G are not rerun or declared resolved here. Passing the targeted current workflows does not close those independent autosave/receipt/audit investigations. Existing datetime/ORM warnings remain visible. The full application suite is not claimed green.

The known NeoSubZero asset mismatch remains explicitly outside this fueling package: `25-neosubzero.css` is 18,771 bytes while `sequence.json` declares 19,118. No SubZero CSS, artwork or manifest change is included. Other previously failing physical-start/markup/ordering cases remain visible; their later assumptions are not automatically deemed obsolete. This report does not claim a full-system pass or reinterpret historical production evidence.

## Next scope and release safety

The next coherent evidence-priority scope is the remaining legacy automatic-start/travel assertions in the physical-start module, independently verifying their resource and trust contracts. The older correction/visit browser autosave/receipt/audit failures documented in R03G remain a separate investigation. The CSS manifest mismatch needs its own narrow asset-integrity correction. Other original R03 groups retain their previous evidence/classification in the updated register.

This release contains only two original test-module repairs, the new scoped contract/PostgreSQL/browser tests and these three audit documents. All other original parent methods are AST-checked against the starting commit. No application code, schema, calculation, operational permissions, source authority, CSP/CSRF policy, FROST model, archive or unrelated asset is changed.

Exact files:

* `tests/test_neoscorpion_fueling_event_foundation.py`
* `tests/test_neoscorpion_fueling_start.py`
* `tests/test_neoscorpion_fob_evidence_contract.py`
* `tests/test_neoscorpion_fob_evidence_postgres.py`
* `tests/browser/test_neoscorpion_fob_evidence.py`
* `docs/audits/2026-11-03-r03h-truckless-fob-and-legacy-on.md`
* `docs/audits/2026-11-03-r03h-fob-evidence.json`
* `docs/audits/2026-11-03-r03h-backlog-register.json`

No production fuel transfer, FOB verification, inventory deduction, aircraft/employee change, Google/R2 write or historical repair is performed. Existing main/Render AutoDeploy is used, with no manual deployment or hosting/configuration change. Exact final SHA/deploy identity, bootstrap/worker startup, public health/login, read-only authenticated pages and rollout checks are recorded after push under ignored `instance/r03h` and in the completion response. Dispatch is opened only after confirming browser-triggered automatic actions are disabled.
