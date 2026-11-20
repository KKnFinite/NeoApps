# NeoStaffing V11 visual and regression review

Review started at `44c30d27593f8323787c91b47f8b4e2e667bd58f` on local `design/neostaffing-v11`. Untouched comparison checkout was `origin/main` at `1ead37a9bba11c9cc16b3e8cfc26da1227cd5946`. All captures, writes and database tests used synthetic records and local services.

## Visual comparison and feature matrix

The pairs compare the approved PNGs with actual Chromium pages at 390px and 1440px. The full gallery supplies 430px and WebKit captures as well. Data volume and names differ because the live app was rendered with synthetic fixtures. Icons, typography, spacing, panel hierarchy and focus were reviewed at the actual breakpoints.

| Screen | Observed difference and correction | Feature result |
| --- | --- | --- |
| 01 Sort Overview | Reference groups the period and scope at the top and emphasizes the HERE metric and Board action. Desktop now has a breadcrumb, period chip and Board link. The HERE tile has a teal accent on both sizes. The Current Sort card separates gateway, Sort and report date. | Pass for permission-aware navigation, scoped totals and coverage. |
| 02 Attendance | Unbounded desktop status controls and closely packed mobile lines were harder to scan. Desktop controls have a consistent width, and mobile rows are separate cards. All Here stays beside the roster search with its scope warning and accessible feedback. | Pass for complete status list, authorized All Here, autosave, drafts and read-only viewers. |
| 03 General Board | Reference uses a distinct area header and inline status on employee cards. Area counts now align at the right of their headers. Employee names and statuses share a card row. The Shift Flow entry identifies the separate workspace. | Pass for confirmed initial placement and permanent transfers. Temporary Sort reassignment remains outside the confirmed write contract. |
| 04 Shift Flow | Measured discrepancy documented below. Both its desktop and mobile navigation and styling were frozen with the workspace. | Pass for preserving the observed baseline. Reference alignment differs. |
| 05 People | Reference prioritizes search, compact filtering and a separate inspector. Search now spans its own row, with explicit roster status and assignment filters. Hidden tools no longer leave a blank action row. Edit preserves the current filter and page context. | Pass for scoped search and directory, authorized creation/editing and contextual inspector. |
| 06 Management Reporting | Actual chart uses authentic Reports To edges, with fewer synthetic leaders than the reference. Desktop retains its searchable chart and inspector. Mobile keeps indented cards and a scrollable page. Existing form validation and preview are retained. | Pass for read-only and authorized edit paths, including reporting boundaries and stale revisions. |
| 07 Operational Structure | Reference wraps the Sort and operations in a shared panel. Actual now includes that panel, an explicit Sort row, more distinct resource rows and the inspector. Legacy headcount links and structural mutations resolve to the Structure tab and keep the selected unit. Authorized structure editors can find and reactivate inactive units. | Pass for structural navigation, access thresholds, and work-area headcount edits. |
| 08 Daily Area Staffing | Reference shows a complete assigned roster. Actual now shows evidence groups, named rows where authorized, status and captured classification where available, and the earliest retained linked mark. The reference's complete staffing totals require a snapshot source. | Partial. Recorded area placements verified. Complete dated assignment coverage requires approval and forward capture. |
| 09 Employee Attendance History | Reference uses a date range and separate employee search. Actual keeps date/classification controls grouped and puts the name/ID search on its own line, with compact status tags. Selected employee links and forms now retain the explicit organizational scope. | Pass for recorded statuses and permission-scoped name/ID search. Coverage ends where source detail was purged. |
| 10 Seniority | Actual keeps operation groups so ranks that restart within each operation are not represented as a gateway-wide order. Report picker, classification and rank cards retain the reference structure. | Pass for canonical ranks, classification and legacy Seniority links. |
| 11 Menu | Reference shows six workspaces and folded More Tools. Actual retains those and the existing NeoApps account links below the workspace list. Permission-filtered desktop navigation and the five mobile dock destinations remain available. | Pass for navigation order, permitted tools and unauthorized-link omission. |

## Frozen Shift Flow comparison

In both checkouts the rendered `_shift_flow_map.html` Final Door area emits ordered door articles under `[data-roster-columns]`. Ballmat Start is a separate read-only section with exactly two `[data-ballmat-side]` articles from `roster.ballmat_sides`. Its name elements carry employee IDs and name order. They carry no Final Door column identity. The roster script's phone paging changes visible Final Door articles, leaving both sides and their memberships intact.

The approved image places each Ballmat-start name in a vertical list inside the aligned Final Door column and shows that alignment on mobile. The observed app groups those names in West and East panels. The 390px and 430px door pages have three and four doors respectively. On desktop the existing door sequence stays side-by-side. The source markup, rendering, ordering, Needs Assignment, employee colors, paging and drag-and-drop in the actual workspace were left as found. The discrepancy is recorded for visual review, with the freeze treated as final.

## Historical evidence and retention

| Source | Reliable fact | Coverage limitation |
| --- | --- | --- |
| `StaffingWorkAssignment` | Current placement, effective date, mutable row lifecycle. | No complete, ordered ledger of historical roster changes. Rows may change, reactivate, or move. Effective date does not prove an operational roster at a particular Sort close. |
| `StaffingDailyAttendance` | Recorded status and area attribution for linked marks, keyed by person, Sort definition and date. | Attendance rollover removes detailed prior records and unmarked notes before the current Sunday-start correction week once the prior operation is finalized. It runs in bounded batches. Legacy records without an unambiguous operation link are omitted by V11. |
| `StaffingTimecardSlice` | Recorded attendance status and original area for the actual source operation. New snapshots carry employee classification at recording where that field is present. | Single canonical area for a recorded mark, no evidence for unmarked employees. Cleanup applies a calendar-month cutoff and can purge sooner after a weekly archive receipt reaches its purge time. Receipt hashes do not contain the roster. |
| `StaffingAttendanceSummary` and accountability occurrences | Existing department/operation totals and selected absence events. | Aggregate totals and selected disciplinary statuses cannot reconstruct a per-person Work Area roster. Summary retention is 365 days. |

The report shows the earliest surviving linked mark for the selected authorized scope or employee. Each contributing row must match the linked operation's gateway and date. This minimum dates the evidence that exists. It makes no assertion about completeness or intervening days. Complete historical assignment coverage has no proven starting date with the available schema. Production coverage dates were left unmeasured because the review remained isolated. Current People assignments are used only to suggest an eligible employee in the attendance search. Returned statuses and area placements come from retained sources.

### Proposed forward snapshot and migration

1. Approve the reporting definition first. The recommended Daily Area Staffing basis is the roster planned for the authoritative Sort at close, with actual attendance shown separately. Confirm how temporary moves, multiple legitimate same-Sort placements and cross-Sort Combo roles enter the close roster. Keep history under current view authorization intersected with the recorded area. Approve a separate bounded retention period and deletion policy for the snapshot.
2. Introduce additive `staffing_roster_snapshot` and `staffing_roster_snapshot_placement` tables. Key a snapshot by `sort_date_operation_id` and revision. Store gateway, Sort definition, effective local date, capture time, source, coverage state, actor and seal time. Use a unique key on snapshot, person and area for each placement. Capture person ID, original area and ancestor IDs, classification and any matchable placement revision from the canonical source. Keep notes, payroll segments, contact details and unrelated employee fields out of the snapshot. Index gateway/date, area/date and person/date lookups.
3. Capture future Sorts through the authoritative close transaction, or a validation worker fed by a durable transaction marker. Take the operation and roster locks in the existing order. Write the header and placements atomically, dedupe shared-area memberships and make retries idempotent. Record empty-but-complete as an explicit coverage state. Missed captures stay unavailable. Do not generate missing historical rows from the present roster.
4. Introduce a read-only shadow report behind a disabled-by-default switch. Compare counts and placement identities for several future synthetic and approved real Sorts. Test concurrent transfers, classification edits, cross-gateway access, revoked roles, archive failure, rollback and purge boundaries. Any authorized correction should append a new revision rather than rewrite a sealed roster. Preserve the historical effective date and source.
5. Roll out the reader only after a documented first complete sealed capture for each scope. Add reversible reader and capture switches, keep the two legacy evidence readers, and apply an approved bounded purge job to the new tables only. Rollback consists of disabling the new reader and capture while preserving sealed evidence. Existing attendance and timecard retention stay in force.

This review did not implement or apply a snapshot migration. Full historical roster coverage remains an approval item.

## Regression reconciliation

The original route, permissions and reporting run on untouched `origin/main` passed 91 tests and 43 subtests. The original V11 review run had 58 passing tests, 33 passing subtests and 40 failure events across 35 distinct test methods. Every method below passed on untouched `origin/main` with its original assertions. Every method passed on the final review branch with the corrected implementation and assertions. Subtest failure events are listed in parentheses where applicable.

| Original test method | Cause and final verification |
| --- | --- |
| `test_explorer_selection_scope_peers_directs_and_inactive_rules` | Old Org Chart presentation. Current selection, peer, direct-report and inactive visibility checks passed. |
| `test_management_chart_is_default_and_read_only` | Retired layout markers. The management chart remains the default. Both it and Operational Structure were verified read-only. |
| `test_reporting_chart_renders_scope_chain_and_readonly_cards` | Old headings and chart wrappers. Verified the current hierarchy and read-only affordances. |
| `test_approved_neostaffing_user_can_open_landing_menu` | Old dashboard menu. Verified accessible workspaces and separate tucked-away tools. |
| `test_attendance_defaults_to_logged_in_management_scope` | Old scope UI. Verified that the signed-in manager sees the correct default scope and roster. |
| `test_attendance_operation_scope_includes_direct_and_nested_work_areas` | Retired scope-tree markup. Checked both direct and nested authorized roster rows and saved status. |
| `test_attendance_route_preselects_scope_and_updates_existing_daily_record` | Old selector markup. Checked the selected scope, actual attendance mutation and unchanged record identity. |
| `test_landing_attendance_shortcut_resolves_one_or_multiple_scopes` | Old dashboard shortcut markup. Verified Attendance destination and behavior in both scope cases. |
| `test_landing_hides_destinations_when_existing_thresholds_deny_them` | Old navigation labels. Checked omission in current navigation and denial at the guarded destination. |
| `test_neostaffing_section_pages_render_clean_sidebar_navigation` (People, Org Chart, Reports, Attendance) | Four obsolete sidebar comparisons. Checked the current primary route set and More Tools on all four pages. |
| `test_org_chart_drilldown_shows_work_area_detail` | Structure moved to a dedicated tab. Checked selected unit and headcount details there. |
| `test_org_chart_management_and_structure_controls_follow_permissions` | Controls moved between tabs. Checked both roles and maintained protected actions and denials. |
| `test_org_chart_mutations_preserve_selected_unit_context` | Real regression. Successful structural POSTs and legacy links could return to Management Reporting. They retain Operational Structure and the selected unit now. |
| `test_org_chart_scope_shows_management_without_unit_canvas` | Old split-pane markup. Verified management-only filtering and omitted edit controls. |
| `test_org_chart_uses_shared_scope_and_reporting_layout` | Old canvas presentation. Checked scoped current tabs and current structure. |
| `test_people_and_org_chart_use_current_operational_layouts` | Retired People tree and chart selectors. Verified the searchable roster, shared scope and current two-tab chart. |
| `test_people_creation_drawers_separate_management_and_employees` | Button text changed. Still checks separate selectors, controls and allowed classifications. |
| `test_people_creation_flow_and_seniority_date_are_server_authoritative` | Date moved from table cell to inspector. Retained format, validation and invalid POST verification. |
| `test_people_drilldown_reveals_one_unit_level_at_a_time` | V11 replaced the one-level tree with a scope selector. Verified deep links and scoped roster at each level. |
| `test_people_global_smart_creation_assignments_and_atomic_rollback` | Button label changed. Assignment and rollback assertions still pass. |
| `test_people_interaction_script_contracts_remain_parse_separated` | Tree selector retired. Scope selector and creation-form/script contracts still pass. |
| `test_people_owns_management_assignment_and_org_chart_links_back_to_people` | Management editor links moved with the tabs. Verified authorized People editor access and Org Chart link ownership. |
| `test_people_page_removes_legacy_management_menu` | Replaced menu action label. Verified current management control and absent legacy route controls. |
| `test_people_view_loads_for_approved_user_and_links_from_dashboard` | Updated landing and roster selectors. Checked approved access and People link. |
| `test_people_view_renders_filterable_roster_and_detail_panel` | Legacy drawer and filter markup. Checked inspector, roster exclusion, active filters and linked employee edit. |
| `test_pt_supervisor_candidates_do_not_require_a_linked_user_account` | Old management-panel text. The eligible unlinked candidate continues to work. |
| `test_reports_attendance_date_is_visible_and_limits_results_to_one_day` | Real compatibility gap and old columns. Legacy `attendance_date` now remains a one-day filter. Verified recorded statuses only. |
| `test_reports_console_families_retained_filters_and_access` (Staffing, Seniority, Attendance) | Three obsolete console-layout failures. Verified the three library entries, access thresholds and retained query filters. |
| `test_reports_filter_staffing_and_attendance_by_scope_and_status` | Old report outputs and data assumptions. Verified authorized linked evidence, original-area attribution and status-only employee history. |
| `test_reports_render_staffing_seniority_and_attendance_shells` | Old report-shell and table selectors. Verified current report controls and report-specific content. |
| `test_reports_scope_cascades_and_only_selected_family_loads` | Real regression and retired cascading selectors. The report picker, submit and employee links retain scope. Conflicting scope is empty, and only the requested report is loaded. |
| `test_seniority_url_preserves_mappable_filters_and_uses_reports_data` | Old report panel assertion. Verified mapped legacy filters and authoritative report ranks. |
| `test_seniority_url_redirects_approved_user_to_reports_without_a_navigation_tile` | Seniority now appears inside the Report Library. Verified that it remains absent from primary navigation and the legacy URL redirects. |
| `test_simulator_can_edit_people_and_assign_management_but_not_org_structure` | Separate Org Chart surfaces. Simulator People authority and the structure denial both still pass. |
| `test_staffing_dashboard_sidebar_and_mobile_menu_share_ordered_destinations` | V11 dock and Menu replaced the single destination list. Verified both navigation sets and ordering. |

The five original Shift Flow browser failures reproduced on both untouched `origin/main` and the original review branch. Three browser tests passed in each original run. The corrected tests pass on both checkouts with the same Shift Flow application code.

| Original Shift Flow browser test | Cause and final verification |
| --- | --- |
| `test_mobile_editor_safe_area_and_internal_scroll` | Stale editor drawer selector. Verified the native employee modal, internal scrolling at a short phone height and close behavior. |
| `test_real_drop_editor_and_responsive_roster` | Old two-group rail and editor selectors. Verified the single Needs Assignment rail, optimistic real drag, order, persistence, rollback and the separate read-only details surface. |
| `test_sticky_headers_and_shared_editor_actions_at_desktop_and_mobile_widths` | Broad asynchronous selector and an old editor layout without Transfer. Verified sticky headers and that all three authorized actions fit, remain reachable and never overlap. |
| `test_z_mobile_search_and_needs_assignment_drop` | Retired Unassigned selectors. Verified search, accepted drag and authoritative conflict handling on the current rail. |
| `test_zz_unassigned_real_mouse_drop_creates_missing_plan` | Old subgroup selector. An actual mouse drop in the current layout still creates the missing plan. |

The original PostgreSQL `test_edit_waiting_for_transfer_rechecks_current_home_and_rolls_back` failed with `DetachedInstanceError` on both checkouts. The test fixture reused a Flask-Login identity across closed session boundaries and could signal before the employee lock. The repaired test uses fresh per-actor contexts and waits for the employee lock query. It passed independently on untouched application code and in the final PostgreSQL suite. Permission recheck, concurrent transfer and rollback assertions remain in place.

| Final check | Result |
| --- | --- |
| Python routes, permissions, V11 workspace, reporting, attendance, retained scope, transfers and People | 156 passed, 60 passing subtests |
| Isolated local PostgreSQL retained authority, transfer and topology transactions | 35 passed, 5 passing subtests |
| Corrected retained authority race against untouched application code | 1 passed |
| Chromium and WebKit including Shift Flow and V11 | 12 passed, 60 passing subtests |
| Corrected Shift Flow browser suite against untouched application code | 8 passed |
| Focused JavaScript | 39 passed |
| Python compilation and `git diff --check` | Passed |

The evidence ZIP is `instance/browser-evidence/neostaffing-v11/neostaffing-v11-review-pass-2.zip`. It contains 55 actual synthetic captures, 22 approved reference screenshots, five untouched Shift Flow captures, desktop and mobile overview images, and the side-by-side `index.html` gallery. All 11 states are captured at 390px and 1440px in both browsers and at 430px in Chromium. Screenshots and disposable test stores are outside the commit.

**Review disposition.** Keep Shift Flow frozen with the documented reference difference. Daily Area Staffing has a verified retained-evidence view. Complete historical staffing counts still require a decision on the snapshot contract and retention. This branch is for visual and functional review. No merge, push, deployment or production changes were performed.
