# Shared node attendance contract

NeoStaffing owns attendance records, sort identity, stale-write protection and
accountability policy. Sektor/Ermac supply operational roster scope and use the
node-specific write contract below; central Staffing retains leadership authority.

`neostaffing_attendance_authority.attendance_authority(user, hierarchy)` resolves
the active StaffingPerson through the existing normalized employee-ID account
link (ambiguous links fail closed), then expands active leadership assignments:

- PT/FT/20C supervisors and FT Specialists: covered Departments.
- Managers: covered Operations.
- Division Managers: covered Sorts.
- Multiple assignments form a union. Inactive people, units/ancestors and
  assignments grant nothing. Specialists' existing Operation assignments cover
  its Departments.

For central Staffing, application roles, `User.management_level`,
`neostaffing.attendance.take`, and Reports To do not grant attendance authority.
Reports To does not filter Sektor/Ermac operational rosters.

`save_attendance` and default `save_operational_manage_attendance` calls validate
leadership against canonical Work Assignments. Explicit Sektor/Ermac calls
revalidate `node_attendance_authority` at the same shared write boundary.
Node adapters retain the current Night operation; general
Staffing can select a configured Sort through the same current-operation resolver.
Both retain signed-original,
stale-row and ordered parent-lock protection and the existing Call In/No Call
occurrence synchronization. The operational writer loads assignments after
locking employee parents; the general writer revalidates them after locking.
Neither role changes nor a forged area ID bypass this check.

## Sektor

Dashboard → EMPLOYEES → EAST BALLMAT / WEST BALLMAT / DISCHARGE. Area choices
use existing node view access and linked management (Grandmaster retains the
viewing exception). Roster queries use active canonical
Night/Ramp/Shift Home assignments, not later Shift Flow or attendance placement
snapshots. The selected area includes its whole population, without Direct
Reports or leadership filtering. All writes still validate area and authority.

`node_attendance_authority(user, workspace, area)` requires an active linked
Staffing management employee AND the existing node edit permission:

- EBM: `neosektor.ebm.edit`
- WBM: `neosektor.wbm.edit`
- Discharge: `neosektor.discharge.edit`
- Ermac saved doors: `neoermac.door_view.edit`

Tunnel Conductor is not umbrella authority. Grandmaster without linked management
cannot write. Node attendance and node Times use the same grant; the Times save
endpoint rederives permitted area IDs rather than trusting client IDs. The
existing configured FT Combo partner-slice rule remains intact. Central Times
reports and accountability retain their leadership-based authorization.

Attendance selections autosave one signed employee original at a time. Successful
responses update the signed original and canonical counts; failures restore the
last acknowledged selection and require reload to verify, without retrying.

The current schema has no semantic area key: the adapter reuses Staffing's
existing normalized area names inside the validated Night/Ramp/Shift hierarchy
and thereafter passes canonical unit IDs. It does not match employee display
strings or infer areas from later movement. No schema/backfill is required.

Without a current Night operation, persistent roster/flow remains visible,
without attendance status, inputs, summary or writes. No fake operation is made.

## Existing and future consumers

Ermac retains saved-door roster/coming presentation and uses the node contract
for editing. The general Staffing attendance UI still uses leadership scope.
Future integrations must define their operational boundary explicitly and reuse
the shared writer; do not introduce duplicate attendance records or assume
Sektor/Ermac grants apply to other nodes.
No additional node screen integration is included here.

Authority reads are set-based (one actor/leadership SELECT, with an already-loaded
hierarchy where available); no per-employee permission queries. Sektor caches
presentation scope only in the current request; writes reauthorize. There is no
poller or cross-request cache. Approved-access roster GETs perform zero writes.
Existing membership-only legacy access can still backfill PortalAppAccess on
the first shell visit; this is unrelated to attendance state.

## Accountability

Sektor and Ermac Employees link to the shared [Accountability workspace](neostaffing-accountability.md).
Finalized facts, FT two-Sort workdays, policy, reconciliation and delivery remain
NeoStaffing-owned. Either configured FT side's authorized management can deliver
the one shared informal obligation. Master+ formal authority is additional to,
never a substitute for, active management leadership scope. Active attendance
does not prompt reconciliation or create discipline actions.
