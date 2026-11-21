# NeoApps permission threshold audit

Issue [#25](https://github.com/KKnFinite/NeoApps/issues/25). Code baseline `276673a46eb30e82d69db9eddb6848ed766c0ffd`. The audit covers 159 distinct rule keys, including 154 current code defaults and five historical keys from the verified Phase 5 production snapshot. Tests use synthetic accounts.

## Findings

- **HIGH · Missing choices.** The scoped editor removed choices below the minimum recorded in the Phase 5 activation receipt. It also hid the selector if the effective value was outside that list. The cutover captured every existing policy minimum in `AccountSecurityEvent.details.policy_floors`. That value is immutable and remains independent of later scoped overrides. The 72 reviewed operational floors in the inventory remain enforced. For example, Fuel Dispatch edit has a Simulator floor and Fueler edit has an Operator floor. [Cutover](../../app/services/account_scope_cutover.py), [Floor resolution](../../app/services/account_scope_runtime.py)
- **HIGH · Authority checks take precedence.** NeoApps platform security and both MotherBrain permission-management key prefixes require protected Grandmaster authority. Nine scoped provider, API and refresh controls use the gateway administrator predicate before threshold evaluation. Those gateway rules previously offered an editor to qualified administrators even though the scoped runtime ignores the stored threshold for that decision. They now explain the authority requirement and reject threshold writes. Staffing vacation and employee records also retain their protected administrator boundary and their existing downstream HR checks. [Platform guards](../../app/services/account_security.py), [Gateway and HR guards](../../app/services/account_scope_runtime.py), [Effective authorization](../../app/services/permission_rules.py)
- **MEDIUM · Legacy and scoped forms used different vocabularies.** The legacy editor offered five roles. Non-node scoped editors could also offer Grandmaster. Both editors now show Watcher, Operator, Simulator and Master, in that order. Protected choices are visually disabled with an explanation. A saved value outside the permitted options remains visible and survives saving another row. Grandmaster role commissioning remains a separate workflow. [Shared selector](../../app/templates/auth/_permission_threshold.html), [Shared validation](../../app/services/permission_thresholds.py)
- **MEDIUM · An access rule can be read-only.** For most node and application `.view` keys, effective authorization uses app/node access rather than a configurable threshold. The scoped editors explain this without presenting a misleading threshold. Two retired Staffing permission keys have no configurable control. The NeoGateway and NeoBid landing views also lack a configurable threshold. NeoRaiden has no separate registered threshold editor or rule key. [Permission registry](../../app/services/permission_rules.py), [Owner routing](../../app/auth/scoped_access.py)

## Editor inventory and write paths

| Surface | Scope and effective behavior |
| --- | --- |
| `/neoapps/permissions`, `/accounts/security/permissions`, `/admin/permissions` | Platform rules once scoped authorization is active. With the legacy engine, the last two use the original permission editor and existing authorization checks. |
| `/motherbrain/permissions` | MotherBrain-owned rules after activation. Before activation this is a legacy editor alias. |
| `/neogateway/permissions` | Selected gateway's owned rules. Shared integrations, Manual API and refresh administration remain governed by protected gateway authority. |
| `/neosektor/permissions`, `/neoermac/permissions`, `/neoscorpion/permissions`, `/neosubzero/permissions`, `/neorain/permissions`, `/neoreptile/permissions` | Exact node and gateway. A node Master cannot edit other scopes. |
| `/neostaffing/permissions` | Staffing application scope after activation, legacy redirect before activation. Protected HR changes keep the existing platform requirement. |
| Account profiles, approvals, scoped Roles and application access | Assignments and entitlements. Their separate role delegation, protected peers and commissioning rules still apply. |
| Node and Staffing settings | Feature preferences, operational controls and HR resource policies use their existing authorization. They do not expose a separate permission-threshold writer. |

`app/auth/routes.py` handles legacy writes under the administration lock. `app/auth/scoped_access.py` and `app/services/scoped_access_management.py` handle scoped writes with fresh authority, a signed policy review, exact ownership, audit, and transaction rollback. Both use the same allowed-choice validation. Policy reads remain in the existing engine. The release requires no schema change, policy reset, threshold migration or grant changes.

## Evidence and limits

The production baseline columns come from the verified **2026-11-18 Phase 5** read-only inventory and activation receipt. The inventory recorded 159 baseline rules and zero scoped overrides at that time. Current editable values in NeoApps still use the scope's saved override if one exists. The Neon connector did not expose a working project-listing tool and the authenticated local CLI listed an unrelated project. A fresh count of production overrides was therefore unavailable. The dated snapshot is kept separate from the current source defaults, and no production rows were changed for this audit. [Phase 5 evidence and rollout](2026-11-17-accounts-access-phase5.md)

## Verification

72 focused Python and route tests, eight PostgreSQL tests, and one browser test with four Chromium/WebKit editor and viewport cases passed.

- `tests.test_permission_rules` covers the legacy editor, invalid values, effective route minima and grouping.
- `tests.test_scoped_authorization` and `tests.test_account_security_phase1` cover all four effective role levels, Watcher and Operator writes where allowed, the activation floor, unauthorized and cross-scope edits, protected gates, and read-only GETs.
- `tests.test_scoped_authorization_postgres` covers real loopback PostgreSQL locks, concurrent writes, audit receipts, exact-scope persistence and rollback of mixed valid/forged updates.
- `tests.browser.test_permission_thresholds` covers legacy and scoped editors in Chromium desktop and WebKit phone, including persistence after reload and disabled options with explanations.

## Per-key inventory

**W** Watcher · **O** Operator · **S** Simulator · **M** Master · **GM** Grandmaster. This matrix shows roles a correctly authorized administrator could choose. After this change all configurable rule rows display **W/O/S/M** in order, with any role outside the listed editable subset disabled. A dash indicates no available threshold choices. Legacy after this change offers W/O/S/M on configurable keys, with all four disabled on hard platform rules. The scoped runtime applies the reviewed floor plus any separate authority gate.

| Permission key | Owner | Code default | Nov 18 stored minimum | Recorded floor | Legacy choices before | Scoped choices before | Scoped selectable after | Restriction / cause |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `motherbrain.parking_conflicts.view` | motherbrain | O | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `motherbrain.parking_optimizer.apply` | motherbrain | M | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `motherbrain.parking_optimizer.run` | motherbrain | M | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `motherbrain.parking_plan.edit` | motherbrain | S | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `motherbrain.parking_plan.view` | motherbrain | O | W | W | W/O/S/M/GM | W/O/S/M | W/O/S/M | Ordinary threshold |
| `motherbrain.parking_rules.edit` | motherbrain | S | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `motherbrain.parking_rules.view` | motherbrain | S | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neoapps.access_requests.edit` | NeoApps | GM | M | M | W/O/S/M/GM locked | — | — | Platform Grandmaster gate |
| `neoapps.access_requests.view` | NeoApps | GM | M | M | W/O/S/M/GM locked | — | — | Platform Grandmaster gate |
| `neoapps.email_verification.resend` | NeoApps | GM | GM | GM | W/O/S/M/GM locked | GM | — | Platform Grandmaster gate |
| `neoapps.portal.request_access.edit` | NeoApps | W | W | W | W/O/S/M/GM | W/O/S/M/GM | W/O/S/M | Ordinary threshold |
| `neoapps.portal.view` | NeoApps | W | W | W | W/O/S/M/GM | W/O/S/M/GM | W/O/S/M | Ordinary threshold |
| `neoapps.portal_management.edit` | NeoApps | GM | M | M | W/O/S/M/GM locked | — | — | Platform Grandmaster gate |
| `neoapps.portal_management.view` | NeoApps | GM | M | M | W/O/S/M/GM locked | — | — | Platform Grandmaster gate |
| `neoapps.user_management.edit` | NeoApps | GM | M | M | W/O/S/M/GM locked | — | — | Platform Grandmaster gate |
| `neoapps.user_management.view` | NeoApps | GM | M | M | W/O/S/M/GM locked | — | — | Platform Grandmaster gate |
| `neobid.placeholder.view` | neobid | W | W | W | — | — | — | No owned threshold editor |
| `neoermac.building_lineup.edit` | neoermac | S | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neoermac.building_lineup.view` | neoermac | O | W | W | — | — | — | View follows node or application access |
| `neoermac.dashboard.view` | neoermac | W | W | W | — | — | — | View follows node or application access |
| `neoermac.door_view.edit` | neoermac | O | O | O | W/O/S/M/GM | O/S/M | O/S/M | Reviewed floor |
| `neoermac.door_view.enter_actual_pulls` | neoermac | — | O | O | W/O/S/M/GM | O/S/M | O/S/M | Reviewed floor |
| `neoermac.door_view.view` | neoermac | O | W | W | — | — | — | View follows node or application access |
| `neoermac.refresh_settings.edit` | NeoGateway | GM | GM | GM | W/O/S/M/GM | GM | — | Protected gateway authority |
| `neoermac.settings.view` | neoermac | S | S | S | — | — | — | View follows node or application access |
| `neoermac.tug_assignments.edit` | neoermac | M | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neoermac.tug_assignments.view` | neoermac | W | W | W | — | — | — | View follows node or application access |
| `neoermac.upcoming_pulls.view` | neoermac | W | W | W | — | — | — | View follows node or application access |
| `neoermac.view_outbound.view` | neoermac | W | W | W | — | — | — | View follows node or application access |
| `neogateway.landing.view` | neogateway | W | W | W | — | — | — | No owned threshold editor |
| `neomotherbrain.arrival_planning.edit` | motherbrain | M | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neomotherbrain.arrival_planning.run` | motherbrain | M | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neomotherbrain.arrival_planning.view` | motherbrain | O | W | W | W/O/S/M/GM | W/O/S/M | W/O/S/M | Ordinary threshold |
| `neomotherbrain.dashboard.view` | motherbrain | O | W | W | W/O/S/M/GM | W/O/S/M | W/O/S/M | Ordinary threshold |
| `neomotherbrain.departure_planning.edit` | motherbrain | M | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neomotherbrain.departure_planning.run` | motherbrain | M | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neomotherbrain.departure_planning.view` | motherbrain | O | W | W | W/O/S/M/GM | W/O/S/M | W/O/S/M | Ordinary threshold |
| `neomotherbrain.flight_api_auto_poll.trigger` | NeoGateway | S | GM | GM | W/O/S/M/GM | GM | — | Protected gateway authority |
| `neomotherbrain.flight_api_review.edit` | motherbrain | S | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neomotherbrain.flight_api_review.view` | motherbrain | S | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neomotherbrain.gateway_matrix.edit` | motherbrain | S | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neomotherbrain.gateway_matrix.view` | motherbrain | O | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neomotherbrain.google_live_polling.edit` | NeoGateway | GM | GM | GM | W/O/S/M/GM | GM | — | Protected gateway authority |
| `neomotherbrain.integrations.edit` | NeoGateway | GM | GM | GM | W/O/S/M/GM | GM | — | Protected gateway authority |
| `neomotherbrain.manage_api.run` | NeoGateway | GM | GM | GM | W/O/S/M/GM | GM | — | Protected gateway authority |
| `neomotherbrain.manage_api.view` | NeoGateway | GM | GM | GM | W/O/S/M/GM | GM | — | Protected gateway authority |
| `neomotherbrain.manage_sort.edit` | motherbrain | S | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neomotherbrain.manage_sort.view` | motherbrain | O | W | W | W/O/S/M/GM | W/O/S/M | W/O/S/M | Ordinary threshold |
| `neomotherbrain.master_schedule.edit` | motherbrain | S | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neomotherbrain.master_schedule.view` | motherbrain | O | W | W | W/O/S/M/GM | W/O/S/M | W/O/S/M | Ordinary threshold |
| `neomotherbrain.node_refresh_timings.edit` | NeoGateway | GM | GM | GM | W/O/S/M/GM | GM | — | Protected gateway authority |
| `neomotherbrain.permission_rules.edit` | NeoApps | GM | GM | GM | W/O/S/M/GM locked | GM | — | Platform Grandmaster gate |
| `neomotherbrain.permission_rules.view` | NeoApps | GM | GM | GM | W/O/S/M/GM locked | GM | — | Platform Grandmaster gate |
| `neomotherbrain.sort_timeline.edit` | motherbrain | GM | M | M | W/O/S/M/GM | M | M | Reviewed floor |
| `neomotherbrain.sort_timeline.view` | motherbrain | GM | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neomotherbrain.system_settings.view` | motherbrain | O | O | O | W/O/S/M/GM | O/S/M | O/S/M | Reviewed floor |
| `neorain.crew_admin.edit` | neorain | O | O | O | W/O/S/M/GM | O/S/M | O/S/M | Reviewed floor |
| `neorain.daily_briefing.view` | neorain | W | W | W | — | — | — | View follows node or application access |
| `neorain.inbound.block_in.edit` | neorain | O | O | O | W/O/S/M/GM | O/S/M | O/S/M | Reviewed floor |
| `neorain.inbound.edit` | neorain | S | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neorain.inbound.sta.edit` | neorain | O | O | O | W/O/S/M/GM | O/S/M | O/S/M | Reviewed floor |
| `neorain.inbound.view` | neorain | W | W | W | — | — | — | View follows node or application access |
| `neorain.load_planner_lineup.edit` | neorain | O | O | O | W/O/S/M/GM | O/S/M | O/S/M | Reviewed floor |
| `neorain.load_planner_lineup.view` | neorain | W | W | W | — | — | — | View follows node or application access |
| `neorain.outbound.edit` | neorain | S | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neorain.outbound.etd.edit` | neorain | O | O | O | W/O/S/M/GM | O/S/M | O/S/M | Reviewed floor |
| `neorain.outbound.milestones.edit` | neorain | O | O | O | W/O/S/M/GM | O/S/M | O/S/M | Reviewed floor |
| `neorain.outbound.service_fields.edit` | neorain | O | O | O | W/O/S/M/GM | O/S/M | O/S/M | Reviewed floor |
| `neorain.outbound.view` | neorain | W | W | W | — | — | — | View follows node or application access |
| `neorain.rainrock.edit` | neorain | O | O | O | W/O/S/M/GM | O/S/M | O/S/M | Reviewed floor |
| `neorain.rainrock.view` | neorain | W | W | W | — | — | — | View follows node or application access |
| `neorain.refresh_settings.edit` | NeoGateway | GM | GM | GM | W/O/S/M/GM | GM | — | Protected gateway authority |
| `neorain.settings.edit` | neorain | S | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neorain.settings.view` | neorain | W | W | W | — | — | — | View follows node or application access |
| `neoscorpion.apu_rates.edit` | neoscorpion | S | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neoscorpion.dashboard.view` | neoscorpion | W | W | W | — | — | — | View follows node or application access |
| `neoscorpion.fuel_assignments.view` | neoscorpion | O | O | O | — | — | — | View follows node or application access |
| `neoscorpion.fuel_dispatch.edit` | neoscorpion | S | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neoscorpion.fuel_dispatch.view` | neoscorpion | O | W | W | — | — | — | View follows node or application access |
| `neoscorpion.fueler.edit` | neoscorpion | O | O | O | W/O/S/M/GM | O/S/M | O/S/M | Reviewed floor |
| `neoscorpion.fueler.view` | neoscorpion | W | W | W | — | — | — | View follows node or application access |
| `neoscorpion.fueling_board.view` | neoscorpion | W | W | W | — | — | — | View follows node or application access |
| `neoscorpion.hanzo.view` | neoscorpion | O | O | O | — | — | — | View follows node or application access |
| `neoscorpion.history.view` | neoscorpion | O | W | W | — | — | — | View follows node or application access |
| `neoscorpion.refresh_settings.edit` | NeoGateway | GM | GM | GM | W/O/S/M/GM | GM | — | Protected gateway authority |
| `neoscorpion.settings.edit` | neoscorpion | M | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neoscorpion.settings.view` | neoscorpion | S | S | S | — | — | — | View follows node or application access |
| `neoscorpion.truck_driver.edit` | neoscorpion | O | O | O | W/O/S/M/GM | O/S/M | O/S/M | Reviewed floor |
| `neoscorpion.truck_driver.view` | neoscorpion | W | W | W | — | — | — | View follows node or application access |
| `neoscorpion.truck_manager.edit` | neoscorpion | S | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neoscorpion.truck_manager.view` | neoscorpion | O | W | W | — | — | — | View follows node or application access |
| `neosektor.conductor.view` | neosektor | S | W | W | — | — | — | View follows node or application access |
| `neosektor.dashboard.view` | neosektor | W | W | W | — | — | — | View follows node or application access |
| `neosektor.discharge.edit` | neosektor | O | O | O | W/O/S/M/GM | O/S/M | O/S/M | Reviewed floor |
| `neosektor.discharge.view` | neosektor | O | W | W | — | — | — | View follows node or application access |
| `neosektor.driver_routing.edit` | neosektor | — | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neosektor.driver_routing.view` | neosektor | W | W | W | — | — | — | View follows node or application access |
| `neosektor.ebm.edit` | neosektor | O | O | O | W/O/S/M/GM | O/S/M | O/S/M | Reviewed floor |
| `neosektor.ebm.view` | neosektor | O | W | W | — | — | — | View follows node or application access |
| `neosektor.live_counts.view` | neosektor | W | W | W | — | — | — | View follows node or application access |
| `neosektor.settings.edit` | neosektor | M | M | M | W/O/S/M/GM | M | M | Reviewed floor |
| `neosektor.settings.view` | neosektor | M | M | M | — | — | — | View follows node or application access |
| `neosektor.tunnel_conductor.edit` | neosektor | S | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neosektor.tunnel_conductor.view` | neosektor | — | W | W | — | — | — | View follows node or application access |
| `neosektor.wbm.edit` | neosektor | O | O | O | W/O/S/M/GM | O/S/M | O/S/M | Reviewed floor |
| `neosektor.wbm.view` | neosektor | O | W | W | — | — | — | View follows node or application access |
| `neostaffing.app_management.view` | neostaffing | M | M | M | — | — | — | View follows node or application access |
| `neostaffing.attendance.edit` | neostaffing | — | M | M | W/O/S/M/GM | M/GM | M | Reviewed floor |
| `neostaffing.attendance.take` | neostaffing | O | O | O | W/O/S/M/GM | O/S/M/GM | O/S/M | Reviewed floor |
| `neostaffing.board.view` | neostaffing | W | W | W | — | — | — | View follows node or application access |
| `neostaffing.bulk_change.use` | neostaffing | O | O | O | W/O/S/M/GM | O/S/M/GM | O/S/M | Reviewed floor |
| `neostaffing.change_requests.approve` | neostaffing | O | O | O | W/O/S/M/GM | O/S/M/GM | O/S/M | Reviewed floor |
| `neostaffing.change_requests.submit` | neostaffing | O | O | O | W/O/S/M/GM | O/S/M/GM | O/S/M | Reviewed floor |
| `neostaffing.change_requests.view` | neostaffing | W | W | W | — | — | — | View follows node or application access |
| `neostaffing.hierarchy.edit` | neostaffing | M | M | M | W/O/S/M/GM | M/GM | M | Reviewed floor |
| `neostaffing.hierarchy.view` | neostaffing | M | M | M | — | — | — | View follows node or application access |
| `neostaffing.management.assign` | neostaffing | S | S | S | W/O/S/M/GM | S/M/GM | S/M | Reviewed floor |
| `neostaffing.management_assignments.edit` | neostaffing | S | S | S | W/O/S/M/GM | S/M/GM | S/M | Reviewed floor |
| `neostaffing.management_assignments.view` | neostaffing | M | M | M | — | — | — | View follows node or application access |
| `neostaffing.org_chart.edit` | neostaffing | — | M | M | W/O/S/M/GM | M/GM | M | Reviewed floor |
| `neostaffing.org_chart.edit_structure` | neostaffing | M | M | M | W/O/S/M/GM | M/GM | M | Reviewed floor |
| `neostaffing.org_chart.view` | neostaffing | W | W | W | — | — | — | View follows node or application access |
| `neostaffing.people.bulk_actions` | neostaffing | S | S | S | W/O/S/M/GM | S/M/GM | S/M | Reviewed floor |
| `neostaffing.people.edit` | neostaffing | S | S | S | W/O/S/M/GM | S/M/GM | S/M | Reviewed floor |
| `neostaffing.people.view` | neostaffing | W | W | W | — | — | — | View follows node or application access |
| `neostaffing.people_management.edit` | neostaffing | S | S | S | W/O/S/M/GM | S/M/GM | S/M | Reviewed floor |
| `neostaffing.people_management.view` | neostaffing | M | M | M | — | — | — | View follows node or application access |
| `neostaffing.permissions.edit` | neostaffing | GM | GM | GM | — | — | — | Retired |
| `neostaffing.permissions.view` | neostaffing | M | M | M | — | — | — | Retired |
| `neostaffing.planned_staffing.edit` | neostaffing | M | M | M | W/O/S/M/GM | M/GM | M | Reviewed floor |
| `neostaffing.planned_staffing.view` | neostaffing | M | M | M | — | — | — | View follows node or application access |
| `neostaffing.reports.view` | neostaffing | O | O | O | — | — | — | View follows node or application access |
| `neostaffing.seniority.view` | neostaffing | W | W | W | — | — | — | View follows node or application access |
| `neostaffing.settings.edit` | neostaffing | M | M | M | W/O/S/M/GM | M/GM | M | Reviewed floor |
| `neostaffing.staffing_groups.edit` | neostaffing | M | M | M | W/O/S/M/GM | M/GM | M | Reviewed floor |
| `neostaffing.staffing_groups.view` | neostaffing | O | O | O | — | — | — | View follows node or application access |
| `neostaffing.vacation_selection.edit` | neostaffing | W | W | W | W/O/S/M/GM | W/O/S/M/GM | W/O/S/M | HR · platform administrator only |
| `neostaffing.vacation_selection.view` | neostaffing | W | W | W | — | — | — | View follows node or application access |
| `neostaffing.work_assignments.edit` | neostaffing | S | S | S | W/O/S/M/GM | S/M/GM | S/M | Reviewed floor |
| `neostaffing.work_assignments.view` | neostaffing | M | M | M | — | — | — | View follows node or application access |
| `neosubzero.callouts.edit` | neosubzero | S | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neosubzero.callouts.view` | neosubzero | W | W | W | — | — | — | View follows node or application access |
| `neosubzero.coordinator.edit` | neosubzero | S | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neosubzero.coordinator.view` | neosubzero | S | S | S | — | — | — | View follows node or application access |
| `neosubzero.deice_log.view` | neosubzero | W | W | W | — | — | — | View follows node or application access |
| `neosubzero.deicer_mobile.edit` | neosubzero | O | O | O | W/O/S/M/GM | O/S/M | O/S/M | Reviewed floor |
| `neosubzero.deicer_mobile.view` | neosubzero | O | O | O | — | — | — | View follows node or application access |
| `neosubzero.outbound.edit` | neosubzero | S | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neosubzero.outbound.view` | neosubzero | W | W | W | — | — | — | View follows node or application access |
| `neosubzero.pretreat.edit` | neosubzero | S | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neosubzero.pretreat.view` | neosubzero | W | W | W | — | — | — | View follows node or application access |
| `neosubzero.qualifications.edit` | neosubzero | S | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neosubzero.qualifications.view` | neosubzero | W | W | W | — | — | — | View follows node or application access |
| `neosubzero.settings.edit` | neosubzero | M | M | M | W/O/S/M/GM | M | M | Reviewed floor |
| `neosubzero.settings.view` | neosubzero | W | W | W | — | — | — | View follows node or application access |
| `neosubzero.truck_management.edit` | neosubzero | S | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neosubzero.truck_management.view` | neosubzero | W | W | W | — | — | — | View follows node or application access |
| `neosubzero.ucc.edit` | neosubzero | S | S | S | W/O/S/M/GM | S/M | S/M | Reviewed floor |
| `neosubzero.ucc.view` | neosubzero | W | W | W | — | — | — | View follows node or application access |
