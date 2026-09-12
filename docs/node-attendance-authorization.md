# Shared node attendance contract

NeoStaffing owns attendance. Nodes supply a roster scope and presentation, not
permissions, records, sort identity, or accountability policy.

`neostaffing_attendance_authority.attendance_authority(user, hierarchy)` resolves
the active StaffingPerson through the existing normalized employee-ID account
link (ambiguous links fail closed), then expands active leadership assignments:

- PT/FT/20C supervisors and FT Specialists: covered Departments.
- Managers: covered Operations.
- Division Managers: covered Sorts.
- Multiple assignments form a union. Inactive people, units/ancestors and
  assignments grant nothing. Specialists' existing Operation assignments cover
  its Departments.

Application roles, `User.management_level`, `neostaffing.attendance.take`, and
Reports To do not grant attendance write authority. Existing node/application
access still controls entering the node. Reports To only filters MY EMPLOYEES.

Both `save_attendance` and `save_operational_manage_attendance` validate the
actor's organizational authority against canonical Work Assignments at the
shared write boundary. They retain current Night operation, signed-original,
stale-row and ordered parent-lock protection and the existing Call In/No Call
occurrence synchronization. The operational writer loads assignments after
locking employee parents; the general writer revalidates them after locking.
Neither role changes nor a forged area ID bypass this check.

## Sektor

Dashboard → EMPLOYEES → EAST BALLMAT / WEST BALLMAT / DISCHARGE. Area choices
are restricted to the manager's scope. Roster queries use active canonical
Night/Ramp/Shift Home assignments, not later Shift Flow or attendance placement
snapshots. MY EMPLOYEES uses active Reports To relationships; ALL AREA removes
only that presentation filter. All writes still validate area and authority.

The current schema has no semantic area key: the adapter reuses Staffing's
existing normalized area names inside the validated Night/Ramp/Shift hierarchy
and thereafter passes canonical unit IDs. It does not match employee display
strings or infer areas from later movement. No schema/backfill is required.

Without a current Night operation, persistent roster/flow remains visible,
without attendance status, inputs, summary or writes. No fake operation is made.

## Existing and future consumers

Ermac retains saved-door roster/coming presentation and uses the same authority
for editing. The general Staffing attendance UI also marks unauthorized rows
read-only. Future Rain/SubZero/Scorpion/Reptile attendance integrations must
resolve their own canonical scope, intersect it with this authority, and call
the shared writer; do not introduce node attendance roles or duplicate writes.
No additional node screen integration is included here.

Authority reads are set-based (one actor/leadership SELECT, with an already-loaded
hierarchy where available); no per-employee permission queries. Sektor caches
presentation scope only in the current request; writes reauthorize. There is no
poller or cross-request cache. Approved-access roster GETs perform zero writes.
Existing membership-only legacy access can still backfill PortalAppAccess on
the first shell visit; this is unrelated to attendance state.
