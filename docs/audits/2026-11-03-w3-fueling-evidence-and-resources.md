# W3 — Fueling evidence, resources and reporting

Date: 2026-11-03 (America/Chicago). Package W3 from [the final remediation plan](2026-11-03-final-remediation-plan.md).

## Result and scope

**All 41 assigned original W3 IDs pass: 0 remaining, 0 skipped.** Every ID first failed against the unchanged starting release. The [machine-readable register](2026-11-03-w3-fueling-evidence.json) records each ID, original rejection, classification, verified contract and final result. Classifications: **22 invalid fixtures, 18 outdated assertions, one independently reproduced application presentation defect**. Some cases combine both fixture and assertion repair; the register assigns their primary cause.

Starting `main` and freshly fetched `origin/main`: **`65bccb7cf42420a48f65e4c98d76c8f5fa1eb7d2`**. Safe fast-forward synchronization found no newer commit; a second fetch before release confirmed the same source. W1, W2 and W4 remain intact. The pre-release Render LIVE deployment was `dep-dbl9eutbedkc73a2av00` at that exact SHA.

This is a consolidated regression-contract repair. **The only application change is a two-line report-path label mapping in `shell_metadata.py`.** JavaScript assets, templates, business rules, permissions, dependencies and database schemas are unchanged. No operational application defect was demonstrated. No historical performance values are rewritten. This is a W3 pass within the tested scope, not a full-system sign-off.

## Verified operational contracts

The October 16 lifecycle and current canonical services take precedence over older documentation that described preparation as an automatic start:

1. Assignment and saved preparation do not manufacture physical ON. Real truck identity and the canonical ON action are required.
2. Physical OFF releases the truck. Missing final data can still occupy the Fueler; physical OFF is not submission or Dispatcher completion.
3. Fueler COMPLETE ASSIGNMENT submits the current edit baseline. Dispatcher COMPLETE posts the immutable event, snapshots and inventory deduction exactly once.
4. Truck travel/location comes from recorded physical release and frozen parking provenance. Current mission parking, administrative flags and raw timestamps do not prove historical location.
5. Actual participation and immutable policy determine PROF. A raw event with plausible times/gallons is insufficient. A Fueler may qualify while a truck's prior origin remains unverified. Completed cycles preserve their frozen rates when today's settings change.
6. The physical OFF forecast is shared by Dispatch and Fueler. It excludes post-OFF finishing. OBSERVE keeps baseline; APPLY requires three qualified event-level outcomes and uses the bounded weighted service ratio once. Aggregate profile counts alone cannot authorize calibration. Missing demand/rate/location remains unknown.
7. Original FUEL, UPLIFT and DEFUEL retain separate cycle/visit identity, accounting and participation eligibility. A later cycle does not relabel or rescore a prior event. Multi-resource operational work remains permitted and is excluded from single-resource PROF as appropriate.
8. A changed APU/Work safety input changes the SPEAR plan token even if the executable steps stay identical. Unchanged inputs retain a stable token. APU projected burn does not become a transfer or inventory transaction.
9. Report pages must identify themselves as REPORTS in the visible mobile header. They previously fell back to DASHBOARD because their paths were missing from shell metadata. The restored original visible-header assertion and both browser engines verify the correction; hidden page headings are not a substitute.
10. Authorized Dispatcher OPEN intentionally retains review access to historical/completed assignment data; an unassigned/former Fueler does not gain that access. A completed read view does not authorize mutations.
11. Stale permanent Work identity is HTTP **409**. The preexisting correction/visit regression expecting 400 was obsolete; its rejection, preserved readings and conflict assertions remain.

Source review covered `neoscorpion.py`, `neoscorpion_segments.py`, `neoscorpion_participation.py`, `neoscorpion_resource_timing.py`, `neoscorpion_prof.py`, `neoscorpion_spear.py`, `neoscorpion_spear_forecasts.py`, inventory/asset services, current route ownership and the owned Dispatch/Fueler fragments. The verified model is not a restoration of pre-audit automatic ON or raw-event scoring.

## Independently reproduced presentation correction

The final visual review of the real mobile PROF report showed **DASHBOARD** at the top while the page displayed performance reports. `/neoscorpion/reports` and `/neoscorpion/reports/fuel` also failed the original REPORTS header assertions on unchanged main. `_neoscorpion_labels()` had no reports branch, so all report paths used the dashboard fallback. This is a real, low-severity screen-identification defect within an assigned W3 case, not an obsolete expectation. The minimal correction maps the exact `/neoscorpion/reports` path and its `/`-delimited descendants to REPORTS. A similarly prefixed non-report path remains at its prior fallback. Fueler, Dispatch and other NeoNode labels are unaffected.

The original mobile-label test is restored to visible `<small>REPORTS</small>` assertions; new metadata coverage checks report root, Fuel Report, PROF and Truck Rolldown, plus dashboard and non-report boundaries. The report browser checks the visible mobile topbar in Chromium/WebKit. There are no query, permission, mutation or data changes in this correction. General report browser-tab title/shared-shell cleanup remains W5 scope.

## Shared canonical fixture

`tests/neoscorpion_evidence_fixture.py::FuelCycle` is the reusable W3 assignment → ON → readings → OFF → submission → Dispatcher completion fixture. It calls actual service APIs, current edit baselines, expected cycle/tail/truck/permanent Work checks, permissions and transaction locks. It records parking through `assign_tail_to_lane` and observed arrival through the canonical MotherBrain/NeoRain override writer. Clocks and current-operation selection are restricted to the synthetic gateway/Sort; locks and eligibility are not mocked away. Authenticated request-local state is isolated and restored between actors.

Positive event/performance tests now use genuine completed workflows. Direct legacy imports remain only as explicitly **negative/unverified** compatibility fixtures. The original unreliable import variations remain covered: zero gallons, missing/reversed times, missing Fueler, interrupted/held evidence, wrong tail and invalid transfer semantics. They are excluded rather than promoted to positive samples. Genuine prior-Sort events test isolation independently.

The first genuine 1,500-gallon/15-minute job gives Fueler PROF 100, with no invented qualified truck origin. A subsequent genuine 30-minute job gives Fueler PROF 50 and Truck PROF 16 from its proven prior release/travel interval; the combined Fueler value is 67. Inventory progresses 5,000 → 3,500 → 2,000. Changing today's pump setting does not alter those snapshots or scores. The browser report checks the same values, excludes the unverified v3 import, and verifies CSV/copy output and responsive geometry.

The old schema expectation is replaced by `tests/fixtures/neoscorpion_w3_schema.json`, a frozen reviewed inventory of the actual starting release's model tables, additive definitions and constraints. Runtime bootstrap is independently exercised on disposable PostgreSQL; no schema alteration is introduced by this package.

## Consolidated original-case outcomes

| Cluster | Original IDs | Cause and preserved protection | Final |
| --- | ---: | --- | --- |
| APU/SPEAR safety token | 1 | Changed input fingerprint legitimately invalidates token; steps/inventory/accounting remain unchanged. | 1 pass |
| Nightly assignment choices | 1 | Inspect selectable options, not unavailable resources displayed for review. | 1 pass |
| No-current-operation context | 1 | Supply real User identity; expired prior Sort cannot become current. | 1 pass |
| Details layout / numeric wrappers / final row copies | 3 | Current additive controls and complete projection mocks; original actions/identities preserved. | 3 pass |
| Dispatcher status and resource locking | 2 | Genuine canonical preparation/ON/OFF/handoff/completion, semantic eligibility rather than retired tags. | 2 pass |
| Shared Fueler/Dispatcher card | 3 | Canonical physical visit, current Target Onboard/estimate/totals, retained Dispatcher review and Fueler ownership. | 3 pass |
| Hanzo | 1 | Real trusted ON/OFF and event snapshots; separate pending mission instead of reopening history. | 1 pass |
| PROF/live performance | 2 | Actual qualified cycles, immutable rates, unreliable import exclusion, prior-Sort isolation. | 2 pass |
| Load Planner | 1 | Owned console region/canonical person and copy output; no invented planner or read-side record changes. | 1 pass |
| Opening inventory and TOP OFF | 2 | Validated current reading creates opening; negative/missing/capacity errors remain atomic. | 2 pass |
| Physical OFF projections | 7 | Full trusted read projection plus real v4 HTTP integration; qualified history, unknowns and warning priority. | 7 pass |
| Production schema contract | 2 | Current frozen additive inventory plus independent actual PostgreSQL bootstrap. | 2 pass |
| Resource calendars | 1 | Truck released at OFF; unresolved final data retains Fueler commitment. | 1 pass |
| Routes/report/menu presentation | 5 | Four assertion repairs plus the verified missing report-label application correction; legacy FOB/vendor metadata cannot invent current evidence. | 5 pass |
| SPEAR release/readiness/recommendations | 4 | Real release/location snapshots and owned fragments, preserving unknown provenance. | 4 pass |
| SPEAR calibration | 1 | Physical duration excludes finishing; OBSERVE/APPLY math and token assertions retained. | 1 pass |
| SPEAR display mock | 1 | Complete current resource/plan-step shape; mission parking stays canonical. | 1 pass |
| Truck activity/Actual/APU display | 3 | Canonical active/released work, live panel, TOP OFF guard and explicit contradictory legacy diagnostic fixture. | 3 pass |
| **Total** | **41** | **No skipped, expected-failure or deleted original IDs.** | **41 pass** |

## Validation and acceptance evidence

Counts below describe separate runs and overlap; they are **not additive unique-system totals**. The full original 367-ID inventory was deliberately not rerun.

| Run | Result | Evidence / scope |
| --- | --- | --- |
| Unchanged starting W3 inventory | 41 unique original IDs failing; 63 failure events | `instance/w3/before.xml`; passing unittest containers/subtests do not count as successful original IDs. |
| Final original W3 inventory | 41 passed; 29 passing subtests; 0 skipped | `instance/w3/final.xml`; strict register generation rejects missing IDs and failed subtests. |
| Curated NeoScorpion core runner | 300 passed, 0 skipped, 221.127 seconds | `python -m tests.neoscorpion_regression core --json instance/w3/curated-final.json`. Includes continuation, independent cycles, inventory, PROF, direct entry, resource planning and audit protections. |
| Related lifecycle/evidence/security | 111 passed; 28 passing subtests; 0 skipped | Complete Fueler OFF, correction, Dispatcher completion, physical-start, Fueler-handoff, resource-PROF math/integration and live-assignment modules; `instance/w3/related.xml`. |
| Disposable PostgreSQL | 47 passed; 9 passing subtests; 0 skipped; 163.50 seconds | Resource-PROF, correction, segments, submission and lifecycle PostgreSQL modules; `instance/w3/postgres-final.xml`. Independent transactions test concurrent completion, immutable event/tank triggers, exactly-once inventory, stale Work, correction/visit conflicts, rollback and additive bootstrap. |
| JavaScript | 95 passed; 0 skipped | All `tests/js/neoscorpion*.test.js` and `.cjs`, `node --test`; `instance/w3/js.log`. Includes autosave, revision/dirty draft, completion, assignment times, direct entry, TOP OFF and report copy. |
| Chromium/WebKit acceptance | Six passed in one clean combined run; 2 passing engine subtests; 199.58 seconds | `browser-verified.xml`: correction/reopen/audit, A→B→A visit/receipt/inventory, all three submission tests and the report. The final report-label correction is additionally verified in both engines by `browser-prof-label.xml`. |
| Report label regression | Two passed; report and non-report path subtests | `report-label.xml`: visible route labels and unaffected neighboring NeoScorpion/NeoNode labels. |
| Static verification | Passed | Application/changed-test compilation and Unicode check; 19 NeoScorpion JavaScript asset syntax checks; `git diff --check`. |

The original correction/visit acceptance is explicit: `test_chromium_webkit_correction_reopen_and_immutable_completion` and the A→B→A lifecycle browser case passed **before any W3 edit**, as well as after the shared-fixture work. They exercise saved autosave data, correction audit, reopen, receipts, dirty drafts, current source identity and immutable completion. No runtime/browser workaround was required.

The initial broad core/PostgreSQL runs exposed one common old assertion: `OperationalTimelineTest::test_same_tail_pending_actuals_survive_a_new_visit_and_stale_form` expected 400 instead of the established 409. Its canonical workflow and data-preservation assertions remain, and both complete groups then passed. The additional PROF report browser fixture expected performance from unverified hand-built events; its positive evidence now uses `FuelCycle`, while imported v3 evidence stays explicitly excluded. These are closely related acceptance repairs, not changes to operational behavior.

Synthetic screenshots remain under `instance/w3/browser-evidence/`: Chromium/WebKit lifecycle dialogs and dirty drafts, and `w3-prof-{engine}-{1440,1920,390,375}.png`. Raw local JUnit/console files remain ignored and are not production-data artifacts. They are retained, not cleaned up.

Warnings remain visible, principally Python 3.13 naive-UTC deprecations and SQLAlchemy legacy Query.get notices. No warning suppression, blanket skipping or expected-failure marking was added. One intermediate related-suite command named nonexistent `test_neoscorpion_prof.py` and collected nothing; the corrected explicitly listed module run produced the reported 111 passes.

## Remaining scope and release limits

No W3 original ID remains unresolved. One real report-header identification defect is corrected; no operational application defect was established. This does not erase unresolved W5 shared-shell, cache/asset, Rain presentation or other independently retained findings. Recommended next package: **W5**, including the separately recorded NeoSubZero asset-manifest integrity issue, followed by the reserved full-inventory reconciliation. Do not infer a new global 367-ID total from W3 arithmetic.

No Neon CU-hour or production performance savings are claimed from this contract repair and display-metadata correction. Historical records with insufficient participation/location evidence remain excluded/unverified. The tests deliberately do not invent attribution to increase score coverage. Production operational acceptance remains read-only; no live fueling, correction, inventory or historical repair is performed.

## Release and preservation

Only the files listed below are staged. Main AutoDeploy is used; no manual deploy, credential, hosting or configuration change. Exact final SHA, Render deployment ID/LIVE status, bootstrap/Gunicorn logs, health/login and authorized read-only Fueler/Dispatch results are supplied with the release receipt after the containing commit is deployed. Static runtime assets must match committed bytes; CSP/HSTS and the login CSRF meta token are checked.

The tracked tree was clean at start. Six unrelated untracked paths (SPEAR artwork, two prior documents, Rain prototype integration and its Python/JavaScript tests) remain untouched and unstaged. Existing Codex jobs, sessions, histories and unfinished work were inspected and preserved. No job/session cancellation, archival, reset, directory deletion or blocked cleanup was attempted. Existing synthetic PostgreSQL directories are preserved. All database mutations used synthetic isolated SQLite or loopback PostgreSQL schemas; no production employees, timecards, flights, truck inventory, Google Sheets, R2 archives or FROST evidence changed.

## Exact changed files

* `app/services/shell_metadata.py`
* `tests/browser/test_neoscorpion_resource_prof.py`
* `tests/test_neoscorpion_apu_truck_projections.py`
* `tests/test_neoscorpion_assignment_integration.py`
* `tests/test_neoscorpion_current_operation.py`
* `tests/test_neoscorpion_details_layout.py`
* `tests/test_neoscorpion_dispatch_order.py`
* `tests/test_neoscorpion_dispatch_workflow.py`
* `tests/test_neoscorpion_fuel_data.py`
* `tests/test_neoscorpion_fuel_planning.py`
* `tests/test_neoscorpion_live_assignments.py`
* `tests/test_neoscorpion_load_planner.py`
* `tests/test_neoscorpion_nightly_asset_services.py`
* `tests/test_neoscorpion_number_alignment.py`
* `tests/test_neoscorpion_off_projection.py`
* `tests/test_neoscorpion_operational_timeline.py`
* `tests/test_neoscorpion_production_schema.py`
* `tests/test_neoscorpion_resource_availability.py`
* `tests/test_neoscorpion_routes.py`
* `tests/test_neoscorpion_spear.py`
* `tests/test_neoscorpion_spear_calibration.py`
* `tests/test_neoscorpion_spear_display.py`
* `tests/test_neoscorpion_truck_display_updates.py`
* `tests/test_shell_metadata.py`
* `tests/neoscorpion_evidence_fixture.py`
* `tests/fixtures/neoscorpion_w3_schema.json`
* `docs/audits/2026-11-03-w3-fueling-evidence-and-resources.md`
* `docs/audits/2026-11-03-w3-fueling-evidence.json`
