# Canonical assignments and Shift Home

`StaffingWorkAssignment` is the only worker-assignment authority. Non-FT
employees have at most one active assignment. FT Combo classifications may
have one active assignment in each Sort. Sort membership follows the live
Staffing unit hierarchy; no duplicate Sort ID is stored on an assignment.
Global employee totals count distinct people. Work Area/Sort totals count
their scoped assignments.

## Deployment

Run the existing Render pre-deploy command before new workers receive traffic:

```sh
python scripts/bootstrap_database.py
```

The explicit schema sync adds `staffing_people.shift_flow_version`, removes
the old person-only assignment uniqueness constraint, and makes the optional
flow Final Door nullable. Existing assignment IDs/values are retained.
PostgreSQL validation triggers serialize by employee and enforce active
assignment multiplicity against current hierarchy ancestry. SQLite receives
equivalent multiplicity checks; legacy SQLite tables are copied with their
IDs intact when required. Invalid pre-existing assignment topology stops
bootstrap with an error rather than discarding assignments.

Bootstrap reconciles the old `sort_start_work_area_id` compatibility field to
the canonical Night/Ramp/Shift Work Assignment. It removes dormant plans
outside Shift and clears only optional locations outside the Home department,
inactive optional locations, or a Ballmat transition on a non-Ballmat Home.
Repeated bootstrap is safe. No request/startup initialization was added.

## Writes and lifecycle

Home and flow edits lock the employee before reading the authoritative
assignment/plan. The revision includes the employee's flow generation, Home
identity/version and optional plan version, including the no-plan state.
Complete-route writes update Home, Setup, transition and Final Door in one
transaction. Stale edits are rejected; clients never replay them.

The shared ORM flush guard applies lifecycle rules to single, bulk and
approved assignment changes. Entering Shift does not fabricate a plan.
Leaving Shift deletes it; returning starts with unset optional flow. The old
Sort Start field is a compatibility mirror, not a second assignment authority.
PostgreSQL deferred triggers also enforce that invariant at commit without
competing with ORM flush ordering.

## Presentation

West Ballmat serves D21/D24/D26/D29/D32/D34; East Ballmat serves
D1/D4/D6/D9/D13/D17. The desktop map projects each employee through Setup,
Home, waves, cleanup and Final Door. Custom routes are derived, purple, and
retain their exact values. Ghosts are phase projections, never extra people.
Standard targets appear through the route picker or while dragging. Setup
can be unset, the Final Door, or the same-side Ballmat. Mobile retains the
phase board and has a touch-equivalent complete-route picker.

Attendance and Ermac read the canonical Night assignment. Attendance
operation identity, saved overrides, permissions and stale-write rules are
unchanged. Other Sort assignments are never implicitly selected as Home.
