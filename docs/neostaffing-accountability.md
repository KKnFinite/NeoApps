# Shared NeoStaffing accountability

## Source and lifecycle

`StaffingDailyAttendance` and `StaffingAttendanceOccurrence` remain the real
per-operation attendance and qualifying-status projection. Call In / No Call
corrections continue through the existing signed-original, current-operation,
leadership-authorized writers. Tracker settings never gate collection.

Accountability reads join frozen workday/source identities to those occurrence
facts and **existing `StaffingAttendanceSummary` finalization**. No GET creates
facts or finalizes a sort. A qualifying-to-nonqualifying correction before close
therefore disappears from discipline; No Call-to-Call In preserves source identity.
Attendance records and source operation dates are never rewritten by grouping.

Ordinary employees have one source operation per workday. FT Combo employees
select two active configured Staffing Sort IDs in chronological order. Pairing
uses the existing `sort_lookup_window_for_operation` gateway-local windows,
not sorted names, hardcoded pairs or server dates. Adjacent first/second instances
can cross midnight and operation dates. Another first instance closes an earlier
unmatched pair; it cannot borrow that later workday's second instance.

The first qualifying write freezes the selected pair and source identities.
Later configuration changes do not regroup complete or in-progress history.
The strongest source truth wins (`No Call > Call In > neither`), producing at
most one workday fact. Both real source operations must have finalized summaries.
Existing finalization completes a previously missing partner even when that
partner never received a qualifying attendance edit.

Pair lookup is bounded to 256 real operations within ±14 operational dates.
Missing, ambiguous, unconfigured or out-of-bound pairs **stay provisional**; no
fallback to Night, fabricated partner, or premature infraction is used. Configure
FT workdays before attendance begins. Previously unconfigured provisional
workdays are not retroactively guessed/regrouped by changing settings.

The ordinary Staffing Attendance selector now accepts configured Sorts through
the same service; Sektor and Ermac remain Night-only adapters. Rollover remains
the existing user-driven, oldest-first, one-prior-operation maintenance, selected
by Sort. There is no new finalization scheduler, polling or background job.

## Participation, policy and ownership

Current canonical Work Assignments determine organizational ownership. Ordinary
staff use their active assignment; FT staff use assignments in their two
configured Sorts. FT history is employee-wide, never duplicated per assignment.
Operation tracker ON overrides Departments; Operation OFF permits independent
Department participation. For FT, either side ON participates. Evaluate both
current Operations' policies at the same retained finalized count and take the
more severe action, then apply the formal progression guard.

Defaults are code (`1 Verbal, 2 Written Warning, 3 Verbal, 4 Warning Letter,
5 Verbal, 6 Suspension, 7 Verbal, 8 Termination`). Only customized Operation
policies are persisted as compact positive-threshold/action pairs. Policy and
tracker changes do not rewrite employees, counts, recommendations or history.

All employee reads/writes require active NeoStaffing management leadership scope.
Either configured FT side can address the shared informal obligation. Direct
Reports and node roles are not authorization. Master+ Staffing access is an
**additional** requirement for formal resolutions/history and tracker/policy
configuration; configuration requires authority across the affected unit.

## Resolution and reconciliation

Informal delivery covers exact unresolved finalized workday IDs. Later facts
are not covered. One employee parent lock makes the first delivery win globally.
Verbal/Written Warning recommendations supersede; no task/counter is stored.

The latest newly finalized native workday can trigger a formal action once.
Issue, override and no-discipline all consume that identity with a unique
constraint. Resolving it does not resurrect older triggers. Dated imported prior
facts affect the count but do not manufacture newly finalized triggers.
Warning Letter → Suspension → Termination cannot skip, including overrides.
Issuing a recommendation never changes employment status automatically.

Reconciliation is separate from Attendance and only appears for finalized facts
with genuinely incomplete retained history. First Infraction records completeness
without fake facts. Prior history requires exact dates and Call In/No Call;
Skip writes nothing. Master may record an exact retained prior formal action.

Resolutions expose stable IDs, employee, actor, recommendation/decision, trigger,
exact informal coverage, recorded timestamp and gateway-calendar issued date.
These are extension points for future immutable documents, not document storage.

## Schema and rollout

Seven additive tables are registered in the approved explicit `schema_sync`
missing-table path: tracker settings, Combo configuration, frozen workdays,
source bindings, resolutions, coverage, reconciliation. Unique source and formal
trigger constraints prevent duplication; indexed employee/date and FK lookups
support retained projections. Existing attendance/assignment schema is unchanged.

Render's existing pre-deploy `database_bootstrap` must finish before new readers
start. Its explicit pre-deploy bootstrap creates missing tables, seeds only
missing Twilight/Day/Sunrise/Preload roots (no children), and backfills retained
legacy occurrence identities in batches of 500. Legacy collection was Night-only;
backfill preserves singleton source identities, not a retrospective Combo pairing.
Repeated bootstrap is idempotent. No credentials or deployment-specific URLs added.

Nine **calendar** month filtering applies even before physical cleanup. Existing
maintenance deletes at most 250 expired resolutions and 250 workday identities
per invocation, oldest first. A source identity referenced by a still-retained
formal record remains until that record expires, but is excluded from counts.
Source occurrence cleanup and summary retention retain their existing authorities.

## Concurrency and cost proof

Attendance and finalization serialize the source operation using PostgreSQL
`FOR NO KEY UPDATE`, then ordered employee locks. This retains write serialization
while allowing source foreign-key KEY SHARE checks across an FT pair; FOR UPDATE
on both operations reproduced a deadlock and was replaced. Finalized-state
validation runs in the following parent-lock statement, avoiding a pre-wait MVCC
snapshot. Accountability delivery/configuration serializes on the employee parent;
stale facts/recommendation or configuration version reject without replay.

The queue aggregates counts/latest formal actions and filters/orders/paginates
in SQL (25 rows), scoped to authorized people, with no employee N+1. Focused
budgets compare one versus 30 employees; the existing ordinary attendance write
budget remains at most 11 SELECTs. No cross-request cache, persisted running
counts, persisted recommendations, or passive accountability GET writes.

Run focused SQLite tests in `test_neostaffing_discipline.py`; opt-in PostgreSQL
tests require `NEOSTAFFING_TEST_POSTGRES_URL` pointing to a **disposable loopback
`neostaffing_test_*` database**. They create/drop only a unique test schema and
exercise simultaneous delivery, formal trigger resolution, two-sort writes,
and a writer waiting for finalization. Never point these tests at production.

## Deliberately excluded

Timecards; personnel documents/PDFs; signatures/initials; RTS; object storage;
future node Employees implementations. Sektor/Ermac link this shared workspace
without changing their attendance ownership or native employee screens.
