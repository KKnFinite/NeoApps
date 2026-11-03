# R03J — Fueler OFF presentation and eligibility

## Decision and scope

The two original cases reproduce obsolete HTML assumptions. Semantic checks confirm that the existing server and shared Fueler card correctly distinguish truckless preparation, assigned-but-unstarted work, physical ON, physical OFF, Fueler submission and Dispatcher verification. One real presentation defect was independently demonstrated: the disabled truckless OFF explanation said to complete Fuel On Board readings/APU before OFF, although the server correctly forbids physical OFF for truckless work even with verified readings. This release corrects that explanation and preserves eligibility, transactions and the five-stage lifecycle.

Fetched and safely synchronized main at **`cb9e817361a0ec7489937dfae88f370f6e019e45`**. Render independently reported that SHA LIVE in **`dep-dbl4m12vcj2c7383iq00`**. No tracked changes existed initially. Six unrelated untracked artwork/prototype/documentation paths, ignored synthetic databases and other NeoNode work were preserved. No applicable AGENTS.md was found in inspected repository/parent paths.

Reviewed the R03I report/register, R03F physical OFF and R03H evidence reports, October 16 lifecycle, canonical OFF/save/submission/completion services, source/Work ownership guards, route authorization, shared Fueler card and live/autosave implementations. No schema, operational API, template layout or JavaScript implementation changes are made.

## Per-case evidence

Both original methods remain in `NeoScorpionFuelerOffTest` in `tests/test_neoscorpion_fueler_off.py`.

| Original case | Reproduced first failure | Verified contract and retained assertions |
| --- | --- | --- |
| `test_get_never_marks_off_and_ready_card_offers_action` | Global byte exclusion of `>TRUCK OFF</button>` fails because the real card renders a native disabled button. Finding its text alone does not establish eligibility. | A genuinely verified truckless FOB fixture has complete Actual/Remaining/APU and is FOB-ready, yet the specific owned OFF button is disabled with `aria-disabled=true`. Forging its actual form POST is rejected without mutation. Actual Dispatcher truck assignment keeps OFF disabled until canonical physical ON; the unstarted forged POST is rejected. After real ON, the exact permanent Work's button is enabled and its POST records OFF. Full-page, live-panel and per-assignment reads produce zero NeoScorpion writes and preserve persisted state. |
| `test_physical_off_does_not_require_final_tf_and_repeat_is_noop` | `<dt>OFF</dt>` is retired markup. Current shared presentation is `.scorpion-fueler-finish` with `TRUCK OFF HH:MM · CONFIRMED`. | Genuine assigned-truck/ON precedes OFF while final T/F is absent. The displayed local time matches persisted UTC time and actor. Repeated modern POST and service calls do not commit, increment revision, create audits/events or change inventory. Post-OFF final data and separate Fueler submission preserve time/actor. Only Dispatcher verification posts the one transfer event and inventory deduction. |

The second old fixture also had Actual below Remaining with a positive transfer, which would not form a valid downstream fuel-completion scenario. Its replacement uses canonical target 31,000 lb, Remaining 30,000 lb, Actual 31,000 lb and final T/F 150 gallons. No calculation rule is changed. Original method IDs and the other five parent tests/helpers are preserved. AST comparison permits changes only to these two method bodies, plus the scoped helper import.

### Small application correction

`app/services/neoscorpion.py::_fuel_rows()` now explains the existing server guard:

* Truckless with nonzero T/F: **Assign the truck used for positive T/F before OFF.**
* Truckless no-transfer: **No physical truck is assigned. Use Dispatcher Fuel On Board verification; do not record TRUCK OFF.**
* Assigned but unstarted: **Confirm physical TRUCK ON before TRUCK OFF.**

The first two strings match the existing `mark_fueler_off()` rejection messages. `physical_off_ready`, permission checks, locks, identity/revision validation, timing and all mutation behavior are unchanged. Existing hold/review priority remains unchanged.

## Verified controls, integrity and concurrency

`_fueler_card.html:258` renders confirmed OFF for an OFF Work and otherwise only renders the OFF form when editable. The actual button has native `disabled`, `aria-disabled` and its reason when ineligible. Semantic assertions select the exact assignment card/form, examine attributes and source Work query parameters, and submit its real action/data. They do not rely on labels appearing somewhere in a reusable page. The existing legitimate ON/resume action after accidental OFF remains available according to its separate workflow.

Canonical `mark_fueler_off()` locks the current operation/assets and assignment, refreshes source Work under existing ownership rules, validates expected identity, and makes existing OFF an unchanged result. Truckless/unstarted, stale Work, foreign Fueler, read-only Dispatcher, missing assignment and missing CSRF requests are rejected through real routes. Read-only Dispatch OPEN offers no OFF mutation form. Stale data gets HTTP 409 before later completed-assignment checks; a fresh completed baseline separately reaches the immutable-completed guard. No authority is inferred from HTML.

Full, live-panel and per-assignment GET tests instrument INSERT/UPDATE/DELETE for NeoScorpion tables and compare snapshots of assignment, Work, tank, mission, asset revision, fuel audit, tail fuel, event/tank snapshots and truck/inventory state. They prove no physical evidence, submission, completion or deduction is created by these reads. Existing authentication/session bookkeeping is outside that physical-state write assertion.

Additional cases preserve meaningful Actual corrections, no-op saves, physical OFF clock/actor, separate Fueler handoff and Dispatcher event posting. Completed OPEN shows the existing completion evidence read-only. Injecting a failure after OFF mutation rolls back to the full prior state and restores the same eligible action; a real retry succeeds without a premature event or deduction.

Disposable PostgreSQL uses isolated schemas and production integrity guards. Simultaneous duplicate OFF returns exactly one changed result and one unchanged result, one timestamp/actor/revision and one physical-off availability entry. Concurrent final Actual/T/F entry and physical OFF retain both valid changes with two meaningful revisions, no submission/event/deduction. Prior B–I PostgreSQL tests also rerun. No global lock or new background work is introduced.

## Browser evidence and drafts

One new method runs the complete UI sequence in both Chromium and WebKit with separate authenticated mobile Fueler (390 px) and desktop Dispatcher (1440 px) contexts. Each engine uses separate synthetic aircraft, users, trucks and assignments.

The test verifies disabled truckless/unstarted controls, real autosave, actual TRUCK ON and enabled OFF. A deliberately failed autosave leaves Actual 12 only in the Fueler's draft while the server retains 11. The Dispatcher records actual OFF through the existing OPEN dialog. The Fueler's real revision request observes the change; its dirty Actual 12 survives. After a successful save, the pending refresh reconciles to confirmed OFF without losing that value. Final T/F and Fueler submission remain separate; Dispatcher COMPLETE posts one event and changes synthetic stock 10,000 to 9,700. OFF actor/time stay unchanged. Both engines report zero page errors.

Screenshots were captured and visually inspected. Local evidence is retained under `instance/r03j/browser/`: `chromium-eligible-off.png`, `chromium-off-draft-preserved.png`, `chromium-dispatcher-complete.png` and their WebKit equivalents. The older excluded correction/visit autosave/receipt browser investigations are not declared resolved by this targeted passing workflow.

The prior R03I browser rerun exposed a closely related test race. After clicking OFF, it checked generic `TRUCK OFF` text, already present on the pre-action button, then immediately reloaded. The observed failure retained `CURRENTLY FUELING ON A04` instead of travel because the request had not been confirmed before navigation. That test now awaits the actual OFF POST response, asserts HTTP 200 and waits for `.scorpion-fueler-finish` to show `CONFIRMED` before its existing fresh-read/travel assertions. This is a narrowly justified browser synchronization repair based on new evidence, not a change to travel, resource availability or physical OFF. The initial combined run (two passing browser methods, one failing R03I method) is retained separately from the passing final rerun. The first added response matcher overlooked physical-Work query parameters and timed out; it was corrected to compare the URL path while preserving those parameters, with that exploratory failure also recorded.

## Measured validation

| Execution | Measured result |
| --- | --- |
| Two unchanged original IDs before edits | **2 failed**, at exactly the reported global button-text and retired `<dt>` assertions |
| Complete OFF parent module plus four new contracts | **11 passed**, 10.03 s; all seven parent cases pass |
| Scoped PostgreSQL, including both originals and two independent-transaction races | **8 passed**, 23.87 s |
| Prior R03B–I PostgreSQL | **106 passed**, 14 passing subtests, 345.53 s; **114 PostgreSQL methods total** across both runs |
| Expanded selected Python regression | **292 passed / 2 retained baseline failures**, 76 passing subtests, 213.17 s |
| Previously passing selected R03I methods | **All 286 remain passing**, zero regressions |
| New dirty-refresh/OFF Chromium/WebKit browser method | **Passed in both engines**, 44.08 s; zero page errors |
| Prior browser regression | R03G full handoff/follow-up/Truck Driver and R03H truckless FOB methods pass in both engines. R03I busy/release/travel method passes in both engines after the response/confirmed-state synchronization repair, 20.09 s. **Four final passing browser methods / eight engine workflows total**, including the new method; initial failed attempts remain recorded separately. |
| JavaScript | **22 passed**, zero failures/skips; three shared JavaScript files pass syntax checks |
| Compilation/diff | Seven affected Python files compile; `git diff --check` passes |
| Complete original inventory | **78 passed / 289 failed unique original IDs**, all **367 executed**, zero skips, 253.94 s |

The inventory is a fresh bounded execution, not subtraction from the old count. Raw pytest outcomes are **578 failure events / 111 passing outcomes / 355 passing subtests**, separate from unique-method counts. The previous measured inventory was 76/291. Exactly the two scoped original IDs newly pass; **no previously passing original ID regresses**. Peak monitored owned-process memory was **900,558,848 bytes** (about 859 MiB), below the 2 GiB process-tree cap with the existing 4 GiB workstation-headroom guard and 20-minute deadline. No production resource saving is inferred.

The two selected Python failures are retained baseline cases: `test_hanzo_fueling_identity_timing_and_required_warning` (`—` versus `02:15`) and `test_only_final_presentation_copies_rows_not_projection_or_plan` (a non-iterable `object()` mock). Neither is changed or silently closed. Existing datetime and ORM warnings remain visible. No tests are deleted, broadly skipped, marked expected-failure or weakened to achieve these results.

Initial exploratory test-helper failures are retained separately in the [execution evidence](2026-11-03-r03j-off-evidence.json): source query-string parsing, required attribute presence, exact ownership error and expected stale-409 precedence were corrected in the test harness while preserving the existing guards. The [updated original backlog](2026-11-03-r03j-backlog-register.json) retains every original ID and its prior evidence/classification, distinct from failure/subtest events.

## Remaining risks and release safety

The NeoSubZero CSS asset-manifest mismatch, Hanzo timing, older correction/visit browser failures, Dispatch presentation mock, NeoErmac mission identity, staffing/authorization and SQL-budget findings remain separate. Their original classifications and per-ID evidence are retained in the updated backlog. No full-system green claim or production resource saving is inferred from this focused work.

Recommended next coherent scope: independently verify the three remaining `NeoScorpionLiveAssignmentsTest` cases for revision authorization/no-current-sort, read-only bounded active-operation revision, and current-Fueler assignment identity. Prioritize server authorization/current-sort integrity before the associated query budget. Their current failures are unresolved evidence, not confirmed vulnerabilities. Keep Hanzo timing, the Dispatch mock and older correction/visit browser investigations as separate packages; source-authority/mission-isolation and employee/permission groups also remain high-priority backlog investigations.

No production fueling, truck movement, inventory, mission, employee, permission, Google Sheets, R2 or FROST test mutations occurred. No historical repair, schema, hosting, worker, security-policy or asset-manifest change is included. No blocked directory cleanup was attempted. The local PostgreSQL shutdown wait expired, then a read-only status check confirmed no server running; the synthetic directory remains retained.

Exactly these ten files are included:

* `app/services/neoscorpion.py`
* `tests/test_neoscorpion_fueler_off.py`
* `tests/neoscorpion_off_presentation.py`
* `tests/test_neoscorpion_off_presentation_contract.py`
* `tests/test_neoscorpion_off_presentation_postgres.py`
* `tests/browser/test_neoscorpion_off_presentation.py`
* `tests/browser/test_neoscorpion_start_evidence.py`
* `docs/audits/2026-11-03-r03j-off-presentation.md`
* `docs/audits/2026-11-03-r03j-off-evidence.json`
* `docs/audits/2026-11-03-r03j-backlog-register.json`

Release uses main and existing Render AutoDeploy only. Exact final SHA/deployment, bootstrap/worker startup, public health/login, read-only authorized pages, asset digest and rollout checks are recorded after push under ignored `instance/r03j` and in the completion response. Dispatch is opened only after confirming browser-triggered automatic actions are disabled.
