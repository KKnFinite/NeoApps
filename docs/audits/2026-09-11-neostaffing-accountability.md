# NeoStaffing accountability foundation and node audit

Base: `f404bccc51e4acc98f6e145d391c8743095e06e7`.

Scope: source/contract review across the NeoStaffing routes, models, services,
templates, browser controllers and existing backlog, with focused disposable
SQLite tests for changed behavior. No production data was inspected or mutated;
no production/PostgreSQL contention or Neon CU measurements are claimed.
This is not a guarantee that every possible concurrent workflow is defect-free.

## Accountability foundation

`StaffingAttendanceOccurrence` is a compact retained projection of validated
current-Night attendance commands, not a second editable attendance authority.
Identity is employee + canonical SortDateOperation (unique constraint). It stores
date, Call In/No Call, timestamps/actor and an unknown-history marker. There are no
notes, running counts, recommendations, policy settings or formal discipline.

Both Staffing attendance writers maintain it in their existing transaction under
the same ordered employee locks. Call In/No Call switches update the same row;
corrections to other statuses or explicit clears delete it. Rollback undoes both
attendance and occurrence changes. Daily-detail rollover does not delete the
occurrence (there is no daily-attendance foreign key).

Collection has no discipline-tracker setting dependency. Unknown pre-rollout
history is never inferred to be clean. The first occurrence, and subsequent
occurrences while that history remains unknown, have `reconciliation_needed=True`.
This represents **RECONCILIATION NEEDED** in persisted state, not a prompt. No
Attendance/Ermac prompt or save gate was introduced. No historical reconstruction
from expired daily rows is attempted. Reconciliation UI/authority remains future
work. Infrastructure/transaction failures still roll back normally; silently
losing occurrence writes while claiming a successful save is not allowed.

Retention is nine calendar months using gateway-local date, with end-of-month
clamping. `retained_occurrences_query` excludes expired facts immediately, even
if physical cleanup has a backlog. Physical cleanup deletes at most 250 rows per
invocation, oldest first, via a bounded SQL subquery. It runs on actual attendance
writes and the existing Attendance maintenance hook, including without a current
sort. Empty GET maintenance checks are read-only. No new job, timer, poll, cache
or keepalive was introduced. With no application activity, physical cleanup waits
for the next invocation; exact logical retention remains available to consumers.

Deployment: model registered with the existing explicit schema-sync missing-table
registry. Render's existing `python scripts/bootstrap_database.py` pre-deploy path
creates the new table and its unique/check/FK/index definitions before new readers
run. No worker-startup DDL, changed existing columns or Ermac preference migration.
Repeated schema sync preserves populated attendance. PostgreSQL DDL compilation
and fresh/repeated local creation are covered; no live migration was executed.

## Confirmed defects fixed / backlog cleared

| Priority | Location | Evidence and fix |
|---|---|---|
| P1 | `neostaffing.save_operational_manage_attendance` | Reproduced stale blank deleting a newer Call In while the second supervisor edited another employee. Main Attendance already had signed originals/ordered locks; the shared operational writer did not. Reused those checks and locks, added signed hidden originals to the existing shared template, enabled enforcement in Ermac/Sektor route calls. Only deliberately changed rows mutate; same-employee conflicts reject before writes; separate employees remain independent. No Ermac scope/roster redesign. |
| P2 | `neostaffing_notifications._resolve_notification_navigation_state` | Feed fixture had 27 retained unread notifications but navigation reported 28 by counting one expired row. Badge now uses the feed retention cutoff, without another query. |
| P2 | `neostaffing_change_requests.cleanup_change_request_retention` | Reproduced all five expired and all five completed requests being handled despite a test batch of two. Each phase now orders/limits to a fixed 250-request batch and retains locks, events, notifications and child-before-parent deletion. Repeated invocations advance the backlog. |
| P3 | `staffing_people.js` storage-denied backlog | Deferred storage removal exception reproduced after successful Add Person, aborting the remaining Work Area picker setup. Guarded optional `removeItem` just like existing get/set operations. No save/DOM/data behavior changed when storage is available. |
| P3 | daily attendance permission regression test | Known baseline test posted without the required signed original token. Updated it to submit the token from its real GET response; permission enforcement is now actually exercised instead of stopping at outdated-form validation. |

## Node-wide review / preserved contracts

| Surface | Review result |
|---|---|
| Landing, access, permissions | Landing unused-summary removal remains. Route decorators retain app approval and fine-grained permissions; request CSRF protection/token injection remains central. New occurrence service has no public endpoint or client authority. |
| People/seniority/reports | `people_context` SQL filters/count/order/offset/limit and bounded off-page detail lookup remain unchanged. `per_page=all` remains an intentional export/report option. Assignment relationships are loaded for displayed people. Scoped seniority reports use SQL joins; whole-scope PDF generation legitimately materializes its report. |
| Assignments / management / 20C | Bulk assignment still prevalidates, batch-loads existing assignments and flushes once. Direct management assignment continues using `management.assign`; Reports To retains its separate authority. PT multi-area, FT department, Manager operation, Division sort and 20C rules were not changed. |
| Org Chart | Existing-unit type mutation rejection and parent/cycle checks remain. No IDs/children/assignments recreated. Reparent review remains separate from ordinary assignment. |
| Shift Flow | `_locked_shift_flow_plan` locks active employee then reloads plan with `populate_existing`/FOR UPDATE and validates expected version. All drawer/drag paths retain this contract. Board active-person filter and preserved inactive plans remain. |
| Attendance / Groups | Current operation/date validation, selected-area SQL roster, aggregate Group totals and legacy NULL-operation scope remain. Main form stale checks are reused, not weakened. Operational no-sort roster still uses persistent assignment/flow with no attendance controls/reads/writes. |
| Rollover | Same sort/date detail scope, one oldest outstanding operation within bounded lookback, finalize-before-purge, summary retention unchanged. Occurrences survive daily purge. |
| Requests / notifications | Existing authorization, item revision checks, routed approvals, notification dedupe and pagination remain. Fixed retained badge and per-invocation cleanup bound above; additional scaling work remains below. |
| Bulk Change / relationship review | Signed workspace and locked-bundle revision checks remain. Global relationship simulation deliberately needs more context than single-person editing; no speculative narrowing of authority or review graphs. |
| Vacation / schedules | Separate actor/scope checks, capacity/turn/calendar locks and historical immutable schedule overlap validation reviewed. Year-scoped allocation datasets and existing bank/turn rules unchanged. No new financial/leave policy assumed. |
| Browser / schema | Existing escaped text rendering and native controls preserved. Only optional storage failure handling changed in JS. Additive missing-table registration follows the explicit bootstrap, not a passive GET. |

## Remaining findings (not represented as fixed)

1. **P1 follow-up, source-confirmed missing protection; not concurrent-DB reproduced:**
   ordinary People `update_person` and generic Org Unit edits still apply full
   posted values without the attendance/Shift Flow expected-version contract.
   Broad form-version work must account for classification/reporting/vacation
   side effects and reparent review. Choose row-level rejection vs independent
   field merging, then add two-editor tests across those workflows. Do not
   confuse this with already-fixed Shift Flow or immutable unit types.
2. **P2 confirmed scaling:** `change_requests_context` loads active people,
   assignments, leadership, affiliations, and matching requests/items before
   Python approval/scope filtering. The routed retention-scope decoder can also
   accumulate many IDs. Pagination/SQL authority narrowing is a separate bounded
   optimization that needs queue/order/JSON compatibility tests, not a one-line
   `LIMIT` that silently hides requests.
3. **P2 confirmed scaling/contention:** `BulkChangeDataBundle(lock=True)` loads and
   locks broad People/relationship collections to validate its whole-workspace
   revision. A selected-subgraph contract could reduce reads/lock duration, but
   must preserve graph simulation and stale detection. No such rewrite here.
4. **P2 performance candidate, not a measured defect:** management vacation
   context loads year-wide selections/capacity and related leadership before
   rendering authorized areas. Profile representative large multi-area years
   before narrowing; capacity/turn decisions may depend on other areas.
5. **P2 lifecycle/concurrency risk, not a reproduced production race:** operation
   currentness is resolved before attendance employee locks, while rollover locks
   operations. A request straddling rollover should be tested on disposable
   PostgreSQL before altering lock order/current-operation checks. SQLite tests
   here prove stale submitted operations are rejected, not real lock contention.
6. **P3 deferred cleanup:** legacy helper/schema-ensure entry points coexist with
   explicit bootstrap, but may be externally scripted/imported. No deletion is
   justified merely because a normal route does not call them. No speculative
   CSS reorganization or dead-code removal was performed.

## Decisions intentionally not made

- Who can reconcile unknown history, what evidence establishes completeness,
  and how pre-rollout occurrences are imported/corrected.
- Discipline tracker configuration, policy thresholds, recommendations, formal
  records and Discipline Actions UI: explicitly out of scope.
- Person hard-deletion/history policy: current person deletion semantics are
  retained; FK cascade for occurrences avoids orphan facts. Deactivation preserves
  occurrences. If history must survive person erasure, define that separately.
- Inactivity-time physical retention SLA: this design is opportunistic to preserve
  scale-to-zero, not a scheduled guaranteed deletion deadline.

## Focused evidence / performance

- Reproduced failures before fixes: stale operational blank removed a saved row;
  unread 28 vs retained 27; cleanup 5/5 vs bounded 2/2; denied storage removal
  threw and prevented picker setup.
- New accountability tests: both writers, switches/corrections/clear, unchanged
  no-op writes, stale same/different employee forms, wrong/no-current/scope/form
  validation, rollback, daily purge survival, calendar cutoff/batch continuation,
  empty-sort cleanup, schema repeatability and PostgreSQL DDL constraints.
- 1 vs 30 changed employees: same SELECT count, one occurrence SELECT per batch;
  existing 1,500-employee attendance context/save limits (<=10 each) unchanged.
  Occurrence insert/update/delete is one required fact change per affected
  employee, not a running-count update. One bounded cleanup DELETE per real save;
  empty forms do no DML. Attendance GET maintenance adds one small expiry probe;
  no expired rows means no DML. Ermac no-sort GET remains zero-write.
- Shared current-sort/no-sort transitions, existing attendance counts/history,
  deep links, read budgets, main stale forms, notification/request regressions,
  repeatable bootstrap and error-sanitization routes included in focused checks.
- No UI screenshot, production load test, exact CU savings or exhaustive whole-
  repository regression claim. No new polling or cross-request caching.

Final validation: **87 focused Python tests passed**, **5 JS tests passed**;
`python -m compileall -q app`, `node --check app/static/js/staffing_people.js`
and `git diff --check` passed. No existing query-budget assertion was raised.

## Changed files

- `app/models/staffing_attendance_occurrence.py` (new)
- `app/models/__init__.py`
- `app/services/neostaffing_accountability.py` (new)
- `app/services/neostaffing.py`
- `app/services/neostaffing_attendance_history.py`
- `app/services/neostaffing_change_requests.py`
- `app/services/neostaffing_notifications.py`
- `app/services/schema_sync.py`
- `app/neonodes/neoermac/routes.py`
- `app/neonodes/neosektor/routes.py`
- `app/templates/neostaffing/operational_manage_employees.html`
- `app/static/js/staffing_people.js`
- `tests/test_neostaffing_accountability.py` (new)
- `tests/test_neostaffing_daily_attendance.py`
- `tests/test_neostaffing_change_requests.py`
- `tests/test_neostaffing_notifications.py`
- `tests/test_neoermac_employees_roster.py`
- `tests/js/people_text_rendering.test.js`
- This audit/backlog report (new).
