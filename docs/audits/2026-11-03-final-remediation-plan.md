# Final audit triage and finite remediation plan — 2026-11-03

## Decision

**Do not open 289 more test-by-test remediation tasks.** There are three confirmed application issues in this assessment, several large groups of incompatible test inputs/assertions, and remaining behavior requiring verification. Complete the five work packages below, with shared contract fixtures and a single final inventory reconciliation.

The priority correction is **retained timecard visibility after an employee changes work area**. Public PWA assets also incorrectly depend on the vendor authentication boundary. A stale SubZero CSS manifest is a separate low-risk metadata defect. No Critical security bypass was established. NeoScorpion live-assignment/revision authorization and current-sort isolation passed the fresh probes described below; its legacy JSON/selector failures do not demonstrate unauthorized access.

**Final audit closure is not justified yet.** The confirmed timecard omission and unresolved permission/identity/resource paths require closure. Continued HTTP 200 health is not evidence that those contracts are correct. This report makes no application or test correction.

## Identity, preservation and method

Fetched `origin/main` and safely synchronized main. Audited SHA: **`095b2766fd6be41ce35be5cc42531db7ae6d0f74`**. Render independently reported the same SHA LIVE in **`dep-dbl6875ckfvc73cjrv20`**. No applicable AGENTS.md was found in inspected repository/parent paths. The six pre-existing untracked artwork/prototype/documentation paths and ignored synthetic PostgreSQL directories remain untouched.

Primary inputs were the final independent audit, R03/R08 evidence, R03B–J reports, the [R03J 367-ID register](2026-11-03-r03j-backlog-register.json), current services/routes/templates, and the current flight-identity, timecard and resource-evidence contracts. Existing summaries were checked against source and new synthetic reproductions.

The last full original inventory remains **78 passing / 289 failing unique IDs**, with **578 failure events**, as measured in R03J. This assessment does not rerun all 367 or change that total. It reclassifies the remaining evidence by shared cause. A fixture correction has not been made merely because a synthetic counterexample succeeds.

All new mutations occurred only in isolated in-memory SQLite fixtures, with the existing test guard and bootstrap disabled. New probes are ignored local assessment artifacts under `instance/final-plan`, not changes to the regression suite. No production operational, account, employee, Google, R2, configuration or schema mutation was performed. Only this Markdown report is intended for commit. Its self-referential commit SHA and subsequent deployment receipt are supplied in the completion response after AutoDeploy.

## What the 289 failures mean

This is a **primary-evidence census**, not 289 application defects and not a forecast of how many tests will pass after repairs. Categories are mutually assigned for accounting; a case with a bad fixture can expose another defect after setup is repaired.

| Primary classification | Original failing IDs | Interpretation |
| --- | ---: | --- |
| Confirmed application defect | **4** | Two asset-boundary cases, one retained-timecard case and one CSS-manifest case; **three distinct application issues**. |
| Invalid fixture | **122** | 116 destination-only NeoErmac setups, four incomplete mocks and two raw PROF event fixtures. This identifies invalid inputs, not verified downstream safety. |
| Outdated assertion | **21** | Eight exact CSRF-tag parsers; three revision-schema/shared-card selectors; eight inline CSS/JS source-location assumptions; two obsolete opening/current-gallon expectations. Their substantive replacement assertions still must execute. |
| Unresolved | **142** | Insufficient evidence to call the application defective or the test obsolete. Includes real scope/history, calculations, authority, schema/mock and performance questions. |
| **Total** | **289** | No failure is deleted, skipped, marked expected-failure or declared fixed by this report. |

The 116 identity-fixture cases comprise `test_neoermac_building_lineup_geometry` (6), `test_neoermac_linked_door_pulls` (18), `test_neoermac_pull_aggregation` (9), `test_neoermac_pull_integrity` (12), `test_neoermac_tail_presence` (14), and 57 failing `test_neoermac_routes` methods that directly use `_assign_lineup_destination`. Static review shows the helper writes destination text without the required slot identity; its mission factory also omits `master_flight_schedule_id`. Other first assertions in those methods may independently need updating. **116 is the count of demonstrably incompatible setups, not 116 promised passing tests.**

The new classifications supersede only the primary triage interpretation. The immutable R03J register remains the source of original IDs, failure diagnostics and previous measured status. Every remaining ID has exactly one owner in the package census at the end of this report.

## Confirmed application issues

| ID / priority | Evidence and impact | Smallest safe correction and validation |
| --- | --- | --- |
| **D3 — High: retained weekly timecard disappears after work-area transfer** | `neostaffing_timecard_ui.py:22–44` passes the selected node's current work-area IDs into `neostaffing_timecards.py:286–302`, which filters the slice's immutable **historical** work area. Authorization at `neostaffing_timecards.py:133–177` follows the person's **current** assignment. Fresh reproduction: create actual attendance and retained slices, transfer the employee EBM→WBM through `assign_work_area`, then load both real node week routes. Both return 200; neither shows the retained row. The database still contains both slices. The actor is authorized for WBM and the employee/Sort tuple; an authorized read without the historical-area filter returns the retained row, while the same read with WBM IDs excludes it. This is a visibility defect, not demonstrated deletion or miscalculated payroll. The intended distinction is documented in [Timecards](../neostaffing-timecards.md): historical labels remain snapshotted while authority follows current assignments. | Separate current authorized node/employee scope from historical descriptive area when selecting retained slices. Preserve immutable source labels, Sort identity, current management authority, Combo rules, versions and retention. Do not rewrite old slice areas or broadly remove scope filters. Verify transfer, revoked authority, both Combo halves, prior weeks, midnight/Sort boundaries, report exports, no GET writes and stale-edit rejection. Owner: W4. |
| **D1 — Medium: public PWA assets pass through authentication/vendor enforcement** | `neoscorpion_vendors.py:67–87` exempts only endpoint `static`. The broader existing public GET/HEAD allowlist is in `app/__init__.py:451–468`, but is reached after the vendor boundary is registered. With a signed-in ordinary fixture, CSS performs **0 SELECTs**, while manifest, service worker and favicon each perform **2 SELECTs**, including one vendor-account lookup. A synthetic unavailable user loader allows CSS but raises for the manifest. A synthetic vendor identity receives **403** for manifest/service worker/favicon while CSS remains 200. This compromises public asset availability/PWA behavior and creates avoidable database dependence. It does not establish an authorization bypass. | Apply the existing exact public GET/HEAD exemption before loading user/vendor identity. Preserve default-deny on private routes, private QR, unsafe methods and operational APIs. Review the stale-client terminal rejection order separately: a retired flight polling request currently incurs authentication work before its intended early rejection. Test public assets under anonymous, normal, revoked and vendor sessions and database failure; private routes must retain all guards. Owner: W2. |
| **D2 — Low: SubZero CSS delivery metadata is stale** | `app/static/css/sequence.json:157–160` declares 19,118 bytes for `25-neosubzero.css`; the source contains 18,771 bytes. This was already recorded in R03G–J and reproduced by the current inventory. It invalidates the manifest's integrity assertion; it is not evidence that deicing status colors or queue logic are wrong. | Recompute only the current delivery size/digest against the canonical Git/LF source. Preserve historical provenance fields, order, CSS, artwork and operational behavior. Validate every manifest fragment plus the served asset identity. Owner: W5. |

## Shared causes that should not become application rewrites

For D3 reproduction, use the existing `tests/test_staffing_live_ux.py::LiveUxTest` synthetic fixture: `attendance()` creates the current slice; create a September 10 Night operation and `sync_attendance(..., {peer.id: 'call_in'}, ..., as_of=2026-09-12)` for the peer. Commit, retain that slice ID, and call the real `assign_work_area(peer, areas['wbm'])`. GET `/neosektor/manage-employees?area=ebm&view=all&mode=times&period=week&date=2026-09-12` and the corresponding `area=wbm` URL without mocking the roster service. Both are 200, but neither includes that `data-timecard-id`. The WBM actor/employee-Sort authorization is true. An authorized `read_rows` for September 6–12 contains the row without `work_area_ids`; adding WBM's work-area IDs excludes it. Both retained database rows remain. This reaches the substantive risk behind `test_week_uses_retained_slices_not_today_roster_and_keeps_nonhere`, beyond its earlier mock rejection.

**Explicit flight identity, not destination fallback.** [Building Lineup flight identity](../neoermac-lineup-flight-identity.md) and `neoermac_building_lineup.py:187–214,299,498` require MasterFlightSchedule IDs in slots and the unique current mission's matching master FK. Legacy destination text intentionally produces SELECT FLIGHT/needs-resolution rather than choosing a same-destination aircraft. A fresh reproduction using the old integrity fixture yields zero cards and HTTP 400. Creating the explicit master/mission link and selecting that ID through the canonical lineup service yields one card and HTTP 200; a stale conflicting pull then returns 409. Nine existing canonical NeoErmac live-integrity methods, including independent-connection slot races, also pass. Reintroducing destination-only fallback would restore the very identity ambiguity the newer contract prevents.

**CSRF/CSP-era source assumptions.** The shared meta token at `base.html:138–141` now has renewal/authentication/TTL attributes. Eight failing security tests expect `>` immediately after `content`, fail in their parser and never exercise the intended authority assertions. The token is present: a fresh semantic parse followed by real staffing POSTs gets 400 without it and 302 with it, creating exactly one synthetic rule. Shared `csrf.js` and Rain CSS/JS are owned external assets, not inline strings. Test the actual form/asset/nonce and browser behavior; do not weaken CSP or reduce these security cases to token existence alone.

**Evidence-aware PROF/resource timing.** `neoscorpion_prof.py:92–128` and `neoscorpion_participation.py` require immutable settings and verified per-cycle participation. Two old live-performance fixtures create raw events without that evidence and expect inclusion. Four other failures use mocks missing required fields such as `ended_early_at_utc` or `fueler_id`. Seven current resource/PROF integration methods pass, including real physical-work and immutable-snapshot contracts. This does not close every remaining forecast/calibration, historical report or availability question; preserve those as W3 verification, without fabricating ON, location, participation or historical settings.

**Opening gallons are no longer two independent setup inputs.** `neoscorpion_assets.py::select_nightly_truck` captures opening gallons from the validated current reading and preserves that historical opening on later operations. The two old nightly-asset methods expect an independent supplied opening value and validation of that now-unused input. Test the current operator path and negative/capacity/current-inventory guards rather than restoring two competing opening values. Inventory reconciliation remains outside authorization for this report.

**SQL budgets are not automatically production inefficiencies.** The new vendor boundary performs a real security lookup per authenticated non-static request. Many recorded budgets differ by one read. Fresh revision probes attribute one vendor lookup; an active isolation fixture uses 10 SELECTs and zero writes. In a separate scale fixture, one mission and fifty missions both use **11 SELECTs / 0 writes / 1 vendor lookup**. Fixture/authentication setup affects absolute totals; do not combine those samples into one claimed universal count. This is constant-query behavior in that endpoint, not an N+1. Other larger differences, repeated hydration and reads still require SQL attribution. Never remove a security read or inflate every budget blindly to make the suite green.

## NeoScorpion live assignments, current Sort and revisions — former R03K

The relevant six original failures are split by actual risk: four live/revision/schema/UI cases belong to W1; the two fabricated PROF-event cases belong to W3.

| Fresh behavior verified with actual routes and synthetic records | Result |
| --- | --- |
| No current Sort, Fueler/Dispatch revision GET | 200, `current_operation=false`, null operation, revision 0, no-store, zero writes. Added `tail_notes_revision` and Dispatch `tail_revision` explain the old exact-dictionary failures. |
| Approved node read versus action authority | Node `.view` permissions deliberately have no configurable per-screen role threshold (`permission_rules.py:1801–1825,1870–1892`). A valid approved node role may read. The same Operator's Dispatcher mutation is **403**. MotherBrain/admin view thresholds and explicit actions remain separately configurable. |
| Explicit inactive node grant | All four revision/live-panel routes deny via redirect; the grant stays inactive. A fresh anonymous client is also redirected on all four. The previous approved read does not authorize a later revoked request. |
| Foreign gateway, prior Sort and another Fueler's assignment | Exact Fueler GETs return **404**; forged ON POSTs are denied before physical mutation. Zero database writes in measured requests. |
| Caller supplies another operation ID to revision | Response uses the server's actual current operation, not the supplied ID; no foreign operation is selected. |
| Stale operation on full and compact Dispatch panel | Both return **409**. A revision fingerprint is not a grant to download another Sort's panel. |
| Active revision scaling | Constant query counts at one/fifty missions, zero writes; missing revision state is not seeded by the read. |

An initial local probe wrongly assumed that editing a node-view minimum must deny a valid approved node reader. Source and registry review established the current explicit read policy, so the final probe tests real edit denial and node revocation instead. This was an assessment assumption corrected in an ignored probe, not a changed test or application guard. No server bypass was demonstrated in this scope. This is not an exhaustive endpoint/role penetration-test claim.

## Five work packages — finite ownership and exit criteria

Counts assign the 289 remaining IDs once, by behavioral owner. They are triage workloads, **not guaranteed repaired-test counts or 289 required code changes**. Keep all application changes minimal and independently justified; update shared fixtures/assertions in batches only after the canonical contract is proven.

| Package | Original IDs | Priority / complexity | One cohesive deliverable and exit gate |
| --- | ---: | --- | --- |
| **W1 — Flight/mission identity and live authorization contract consolidation** | **141** | High integrity/security verification; medium | Replace destination-only identity setup with one explicit master→mission→slot fixture API, based on existing passing canonical integrity fixtures. Preserve deliberately ambiguous legacy scenarios as negative tests. Batch-update request construction and identity assertions across Ermac routes, linked pulls, aggregation, presence, transport and geometry. Include former R03K's four cases and the remaining MotherBrain HOT/ALP and Rain invalid-time authority cases. Exit: repeated destinations never merge, stale/foreign identities reject atomically, linked-door writes keep ownership, real authorized flows work, GET/revision stay read-only, and canonical PostgreSQL concurrency plus mobile/desktop browser scenarios pass. Fix production code only for a new independent counterexample. Do not restore fallback matching to satisfy legacy setup. |
| **W2 — Public request boundary and attributed SQL budgets** | **34** | Medium reliability; small boundary correction plus medium profiling | Correct D1 with the shared exact public-asset guard, preserving vendor default-deny and private/auth/session rules. Attribute the common vendor lookup and other changed queries in representative Sektor, MotherBrain, Staffing and Scorpion requests. Separate necessary constant authorization reads from true per-row reads, redundant hydration/writes and cold initialization. Exit: public GET/HEAD assets work without DB/auth/vendor lookups even during outage; private/unsafe routes still deny; stale terminal clients stop at the intended boundary; bounded SQL/read-write counts at small/large populations are documented. Adjust only demonstrably obsolete budgets; optimize only measured waste. No process-global permission cache. |
| **W3 — Fuel resource, evidence, timing and reporting contracts** | **41** | High operational verification; medium | Build one reusable canonical physical-visit/event/snapshot fixture for the remaining resource-availability, travel, Hanzo/OFF forecast, calibration, PROF/report, truck display and schema/mock cases. Keep genuine unknowns unknown. Replace incomplete mocks and raw unverifiable event assumptions as one batch. Exit: actual versus preparation, physical release, current cycle/Work, one-resource PROF eligibility, independent UPLIFT/DEFUEL, immutable event/inventory and exactly-once accounting hold; verify old unknown-inventory and opening-snapshot cases through real controls. PostgreSQL races and correction/visit browser failures remain explicit acceptance checks. No change to mathematics or workflow merely for a display assertion. |
| **W4 — Retained staffing records and actual attendance authority** | **24** | **High; confirmed application correction**; medium | Fix D3's current-authority/historical-area selection conflict without rewriting retained records. Consolidate valid linked-management/Home/leadership fixtures and a semantic CSRF-form helper for attendance, vacation, management changes and retained timecard views. Exit: transfers preserve authorized visibility and old labels; unauthorized/revoked users cannot view/edit another scope; Combo halves, day/week/Sort selection, stale version and capacity conflicts work atomically. Run real SQLite/PostgreSQL and browser transfer/timecard scenarios. Do not remove current authority or restore role-only attendance writes. Historical reports and retention must remain unchanged. |
| **W5 — Semantic presentation, CSP/cache identities and asset integrity** | **49** | Medium verification for unresolved controls; low/optional for proven cleanup; small–medium | Correct D2 metadata only. Batch-convert exact retired tag/menu/style-location checks to semantic rendered controls, owned external assets and explicit CSP/cache identities. Cover Rain source-mode presentation, shared mobile/desktop shells, fonts and changed navigation with representative browser scenarios. Exit: asset sizes/hashes match source, cache invalidation is verified without freezing old version strings, drafts/actions/permissions remain protected, and source cleanup assertions are not substitutes for browser behavior. Promote any observed data/permission defect to its existing W1/W3/W4 owner rather than treating it as cosmetic. Unresolved controls are not automatically deferrable. No UI redesign. |
| **Total** | **289** | | **Five packages, no per-test release series.** |

### Shortest safe execution sequence

1. **Correct D3 in W4 and D1 in W2 first.** They have independent reproductions and bounded remedies. Preserve the failing historical probe until its acceptance scenario passes. D2 is a small separate metadata correction in W5, with no CSS/operational edit.
2. **Perform W1 once as a shared identity/authority batch**, not one task per old HTTP 400. Keep the newly verified R03K security behavior as acceptance coverage; do not implement speculative authorization changes.
3. **Close W3 and the remaining W4 operational questions using canonical scenarios.** One physical-visit/resource fixture and one actual-management/timecard fixture should replace repeated incomplete setups. Stop a package if its verified contract conflicts with a proposed behavioral change; record the specific product decision rather than guessing.
4. **Finish measured SQL attribution and optional W5 cleanup in batches.** Cosmetic original assertions may be explicitly deferred with equivalent current contract/browser coverage. They are not permission to suppress a material defect or delete security assertions.
5. **One final reconciliation and sign-off:** execute all 367 original IDs once after the packages, plus the current contract/security suites and feasible PostgreSQL/browser suites. Report unique IDs separately from subtest events. Every remaining failure must have a named shared cause, owner and either corrected coverage or explicit justified deferral. No unresolved High integrity/security issue may be waived as “legacy.” A full-system green claim requires an actual full-system execution, not this inventory alone.

No new infrastructure, background polling, dependency sweep, operational redesign, schema migration, production repair or archive backfill is proposed. R03B–J's legitimate completed repairs remain protected.

## Evidence, execution and limitations

Fresh runs on unchanged audited application/test files:

* Existing suites: **24 authorization-revocation + 9 canonical NeoErmac live-integrity + 7 resource/PROF integration methods passed**. Their combined run with six initial probes reported **46 passed**, 62 passing subtests, 26.71 s.
* Final ignored assessment probes: **8 passed / 1 failed**, 6.60 s. The failure is the intentional retained expectation exposing D3, not a suppressed or expected-failure mark. Additional public-vendor and revision-scale probes are included.
* Across those executions: **49 distinct methods, 48 passing and one reproduced application failure**; repeated executions are not additional unique coverage. Existing datetime/ORM warnings remain visible. The initial incorrect node-view-threshold probe and intermediate diagnostic runs are retained separately.
* R03J's full 367-ID result and its 114 PostgreSQL methods are prior evidence, not newly executed PostgreSQL coverage in this assessment. No new PostgreSQL server was started. The fresh identity suite's independent-connection races use SQLite; they are not relabeled PostgreSQL races.
* No new broad browser run or production workflow mutation was used to make a read-only triage green. R03J browser evidence is prior evidence; older correction/visit browser failures remain W3 acceptance work. The new staffing defect has not been patched or validated after a fix.
* No production data-population count, payroll-error frequency, Neon CU saving, bandwidth saving or incident rate is inferred from synthetic probes. D3 demonstrates a display omission with intact records; actual production incidence and export impact are not measured.
* The census uses source-level fixture/contract review as well as executions. Only the sampled canonical scenarios have fresh runtime proof. A primary invalid/outdated label never proves that all later assertions pass. The 142 unresolved IDs are deliberately not called harmless.

Reproduction artifacts are retained locally under ignored `instance/final-plan`: source/diagnostic grouping, `grouped.json`, probe source, `assessment.xml`, `assessment-probes-final.xml`, logs and `probe-observations.json`. The report includes the critical reproduction and source references so the conclusion does not depend on private production data. Only this report is committed; no application or regression-test file is modified.

Render verification follows observed deployment identity and runtime checks, not a successful push alone. Current official [deployment](https://render.com/docs/deploys) and [health-check](https://render.com/docs/health-checks) references were read (HTML fallback after Markdown content-type retrieval failed). The report-only commit is allowed to AutoDeploy through existing main configuration; no manual deployment or setting change is authorized. Exact LIVE SHA/deployment and the observed bootstrap/health/error window are reported after publication.

## Package census by test module

The following table maps every remaining original ID to a finite behavioral owner. A mixed module is split by actual concern: live revision versus PROF, SQL versus operational behavior, or presentation versus retained records. Counts are from the R03J register and sum to 289. This is ownership, not another list of per-case remediation tasks.

<!-- The module census is generated from the retained original-ID mapping. -->

| Module under `tests/` | Failing IDs | Owner counts |
| --- | ---: | --- |
| `test_db_query_batching.py` | 1 | W2: 1 |
| `test_flight_api_import.py` | 2 | W2: 1, W5: 1 |
| `test_frost_phase10.py` | 1 | W5: 1 |
| `test_local_launch_navigation.py` | 7 | W2: 2, W5: 5 |
| `test_motherbrain_live_collaboration.py` | 3 | W2: 3 |
| `test_motherbrain_routes.py` | 9 | W1: 1, W5: 8 |
| `test_neoermac_building_lineup_geometry.py` | 6 | W1: 6 |
| `test_neoermac_door_supervision.py` | 3 | W1: 3 |
| `test_neoermac_linked_door_pulls.py` | 18 | W1: 18 |
| `test_neoermac_pull_aggregation.py` | 9 | W1: 9 |
| `test_neoermac_pull_integrity.py` | 12 | W1: 12 |
| `test_neoermac_read_budgets.py` | 3 | W2: 3 |
| `test_neoermac_routes.py` | 66 | W1: 66 |
| `test_neoermac_schema_sync.py` | 1 | W1: 1 |
| `test_neoermac_tail_presence.py` | 14 | W1: 14 |
| `test_neoermac_transport.py` | 5 | W1: 5 |
| `test_neoermac_workspace_presentation.py` | 1 | W1: 1 |
| `test_neofontlite.py` | 1 | W5: 1 |
| `test_neofontplain.py` | 1 | W5: 1 |
| `test_neorain_board_layout.py` | 7 | W5: 7 |
| `test_neorain_load_planner_routes.py` | 1 | W5: 1 |
| `test_neorain_outbound.py` | 7 | W1: 1, W5: 6 |
| `test_neorain_ui_corrections.py` | 1 | W5: 1 |
| `test_neoscorpion_apu_truck_projections.py` | 2 | W2: 1, W3: 1 |
| `test_neoscorpion_assignment_integration.py` | 1 | W3: 1 |
| `test_neoscorpion_current_operation.py` | 1 | W3: 1 |
| `test_neoscorpion_details_layout.py` | 1 | W3: 1 |
| `test_neoscorpion_dispatch_order.py` | 1 | W3: 1 |
| `test_neoscorpion_dispatch_workflow.py` | 2 | W3: 2 |
| `test_neoscorpion_fuel_data.py` | 3 | W3: 3 |
| `test_neoscorpion_fuel_planning.py` | 1 | W3: 1 |
| `test_neoscorpion_live_assignments.py` | 6 | W1: 4, W3: 2 |
| `test_neoscorpion_load_planner.py` | 1 | W3: 1 |
| `test_neoscorpion_nightly_asset_services.py` | 2 | W3: 2 |
| `test_neoscorpion_number_alignment.py` | 1 | W3: 1 |
| `test_neoscorpion_off_projection.py` | 7 | W3: 7 |
| `test_neoscorpion_production_schema.py` | 3 | W2: 1, W3: 2 |
| `test_neoscorpion_resource_availability.py` | 1 | W3: 1 |
| `test_neoscorpion_routes.py` | 6 | W2: 1, W3: 5 |
| `test_neoscorpion_spear.py` | 4 | W3: 4 |
| `test_neoscorpion_spear_calibration.py` | 1 | W3: 1 |
| `test_neoscorpion_spear_display.py` | 1 | W3: 1 |
| `test_neoscorpion_truck_display_updates.py` | 3 | W3: 3 |
| `test_neosektor_conductor_queries.py` | 2 | W2: 2 |
| `test_neosektor_conflict_queries.py` | 1 | W2: 1 |
| `test_neosektor_css_cleanup.py` | 1 | W5: 1 |
| `test_neosektor_discharge_controls.py` | 1 | W5: 1 |
| `test_neosektor_discharge_queries.py` | 1 | W2: 1 |
| `test_neosektor_integration_modes.py` | 1 | W2: 1 |
| `test_neosektor_mutation_queries.py` | 3 | W2: 3 |
| `test_neosektor_poll_queries.py` | 5 | W2: 5 |
| `test_neostaffing_attendance_counts.py` | 2 | W4: 2 |
| `test_neostaffing_bulk_change_cost.py` | 5 | W2: 5 |
| `test_neostaffing_change_request_reads.py` | 1 | W2: 1 |
| `test_neostaffing_data_foundation.py` | 1 | W4: 1 |
| `test_neostaffing_discipline.py` | 1 | W4: 1 |
| `test_neostaffing_management_review.py` | 2 | W4: 2 |
| `test_neostaffing_management_vacation_picks.py` | 3 | W4: 3 |
| `test_neostaffing_routes.py` | 13 | W4: 9, W5: 4 |
| `test_neostaffing_shell.py` | 3 | W5: 3 |
| `test_neostaffing_vacation_selection.py` | 5 | W2: 2, W4: 3 |
| `test_operational_shell.py` | 3 | W5: 3 |
| `test_page_assets.py` | 1 | W5: 1 |
| `test_shared_ux_hooks.py` | 1 | W5: 1 |
| `test_shell_metadata.py` | 1 | W5: 1 |
| `test_staffing_live_ux.py` | 5 | W4: 3, W5: 2 |
| **Total** | **289** | **W1 141; W2 34; W3 41; W4 24; W5 49** |
