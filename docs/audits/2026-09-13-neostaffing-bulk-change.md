# NeoStaffing Bulk Change: transaction and query proof

Base: `fae187fc1cce91aa3a6f053ea90d9b8f5bae1da8`.
Test data only; no production database or operational data was used.

## Path and measurements

The signed session workspace is posted to `/neostaffing/bulk-change`.
Stage actions normalize/authorize proposed changes without writing them. Apply
loads a locked bundle, compares the global workspace revision, simulates and
validates the complete package, then mutates through the existing assignment
lifecycle and commits once in the route. Errors roll back the complete package.
PT submission delegates to the existing Change Requests service; it was not
changed. Existing approval-submission tests remain part of validation.

Fresh authenticated HTTP requests were instrumented through SQLAlchemy execution
and ORM-load events. Fixture: 119 people, 112 work assignments, seven units,
seven leadership rows, six active reporting rows. The existing 1,500-person
regression budget is also retained. PostgreSQL 17 proof uses disposable loopback
schemas, including real independent connections/transactions; baseline service
code was loaded from Git without modifying the working tree.

| Request | SELECT before | SELECT after | UPDATE statements before → after |
| --- | ---: | ---: | --- |
| Initial GET | 15 | 15 | 0 → 0 |
| Stage + render | 21 | 15 | 0 → 0 |
| Apply 2 Work Area changes | 15 | 14 | 2 → 2 |
| Apply 100, including 2 already at target | 15 | 14 | 4 → 2 |
| FT Combo selects an existing assignment | 11 | 11 | 2 → 0 |
| Hourly status change, 2 people | 17 | 12 | 1 → 1 |
| Hourly status change, 100 people | 311 | 12 | 1 → 1 |
| Stale Apply + error page | 24 | 24 | 0 → 0 |

UPDATE counts are driver executions, not affected rows. The mixed 100-person
batch previously updated 100 people plus 100 assignments; now only 98 people
and their 98 changed assignments are updated. A real Work Area change still
updates the person revision and assignment as required. No INSERT is needed for
these existing-assignment fixtures. First-use/new-person behavior remains covered
by existing tests. Full apply and status/no-op measurements match PostgreSQL and
SQLite; initial/staging counts are covered on both engines after the change.

## Fixes

1. Stage + render loaded the same six-table snapshot twice. The route now passes
   one read-only request-local bundle through staging and context construction.
   Hydration drops from 238 people/224 assignments to 119/112 per staging POST.
2. The assignment lifecycle reread assignments already loaded under locks. Apply
   now uses its existing `staffing_assignment_snapshot` mechanism, covering the
   purge query's autoflush and restoring/clearing it in `finally`. No cache lives
   beyond that transaction-local use.
3. Staged no-op assignments and unchanged person fields forced timestamp writes.
   Actual field comparisons now suppress those UPDATEs; real changes retain the
   normal revision/timestamp/lifecycle work and existing result-count semantics.
4. Hourly status changes called management-vacation reconciliation per employee,
   even with no applicable state. One SQL UNION checks for retained active turns
   or future uncancelled selections. Actual management employees still reconcile;
   former managers with retained state still reconcile/cancel/advance through the
   existing service. No vacation rules were changed.
5. A retained ORM bundle could mask a committed assignment change: a subsequent
   `FOR UPDATE` query returned cached attribute values and the old revision.
   Reproduced on PostgreSQL with a second transaction using the canonical clear
   service. Locked loads now use `populate_existing()` before revision checking.
   The stale package is rejected, its peer remains unchanged, and a fresh valid
   package succeeds. This adds no queries.

## Locking and remaining limitations — not silently relaxed

The global lock footprint is **unchanged**. Even a two-person package locks all
seven units, 119 people, 112 assignments, seven leadership rows and six active
reporting rows in this fixture; the active affiliation table is also included
(empty here). A PostgreSQL `FOR UPDATE NOWAIT` probe confirms that an unrelated
employee is blocked while Apply holds the transaction. Thus selected-row-only
locking is **not achieved** by this patch.

The bundle supports employee creation, classification changes, management
relationships and unit reparenting, with a global signed revision and whole-final-
state validation. Safely narrowing this requires separating those validation
dependencies from the mutation set and coordinating lock order with other
writers. Merely restricting the existing six lock queries would weaken those
guarantees. This audit does not replace that concurrency model.

Initial rendering and apply still hydrate the full staffing snapshot and its
assignment history. Query count is flat, but memory/read volume scales with that
snapshot. The existing reporting-history purge also retrieves its expired set
without a batch limit. These are remaining scaling limits, not newly bounded
paths. Actual management vacation transitions still use per-manager reconciliation;
this pass removes the demonstrated hourly no-op fanout, not that service's
stateful transition architecture.

## Regression coverage

- Real GET/stage/apply SQL and hydration budgets; unchanged rows and no-op writes.
- Small/100-person Work Area and hourly status batches without per-person reads.
- Stale HTTP post: zero writes, current assignment preserved, peer unchanged.
- PostgreSQL cached-snapshot race and two competing Bulk Apply writers: one
  succeeds, one conflicts; no duplicate active assignments or partial package.
- FT Combo other-Sort assignment/timestamp survives reload and a fresh login.
- Bulk Home change follows existing Shift lifecycle; exit deletes the plan and
  return does not fabricate one.
- Former-management vacation picks and turns still receive required cleanup.
- Existing Bulk Change authorization, PT submission, new-person, reorganization,
  management swap and 1,500-person budget tests; existing assignment/Shift and
  PostgreSQL concurrency tests remain in the focused validation set.

Run the optional PostgreSQL proof with `NEOSTAFFING_TEST_POSTGRES_URL` pointing to
a disposable localhost `neostaffing_test_*` database. Each test uses a generated
schema and drops only that schema. The harness rejects non-loopback/other database
names. No schema/index change, polling, UI redesign or manual deployment is part
of this patch.

Follow-up: [normalized Employee ID integrity](2026-09-13-neostaffing-employee-id-integrity.md)
adds database protection for trim/case-equivalent IDs. It does not narrow these
Bulk Change locks or claim the remaining lock-boundary work is complete.

## Final authorization boundary — 2026-09-14

Base: `11fd1500d64d0aee3af36bcb849170dc36ce81ca`. Disposable PostgreSQL
reproduction found an additional blocker before workspace locks could be narrowed:
an admin's app-access revocation committed before `_validate_direct_authority`,
but the original Apply still committed both assignments using request-cached
access. A fresh request correctly denied the same actor. This occurred with the
existing full workspace locks and normalized Employee ID index installed.

`neostaffing_write_authority.lock_staffing_write_authority()` now refreshes and
share-locks, in order:

1. The actor's **User** row: active state, session version, global role and
   Employee ID used to resolve Staffing identity. A session-version change from
   the authenticated request snapshot rejects the write.
2. That user's **NeoStaffing PortalAppAccess** row: active/approved state and role.
3. Only the **PermissionRule** rows needed by the submitted package: Bulk Change
   always, People Edit for people changes, Management Assign for management or
   reporting changes, and Edit Structure for hierarchy changes. Rules lock in ID
   order. Missing rules/access fail through existing authorization semantics.

These are fresh `populate_existing()` reads, not the identity map or request
cache. The helper clears stale request-cache decisions and primes the existing
permission helpers from the locked records. Apply then reruns Bulk access, actor
classification/role resolution, PT restriction, scope and direct authority before
any mutation. There is no parallel permission policy. Other critical Staffing
writers may reuse the boundary only while also protecting their organizational
dependencies and holding the transaction until commit/rollback.

Organizational dependencies remain in the **unchanged** full locked bundle:
StaffingPerson classification/identity, units, Work Assignments, leadership,
reporting and 20C affiliations. This pass does not narrow those locks or modify
assignment/Shift semantics. No schema changes were needed.

SHARE locks permit concurrent authority readers but block updates/deletions to
the decision's authority rows until the mutation ends. If revocation owns the
row first, final authorization waits and observes the committed denial; if Apply
owns the share lock first, revocation waits until Apply commits or rolls back.
Unrelated users and unrelated permission rules are not locked.

Measured valid-Apply SQL budgets increase by **two net SELECTs**, not per person:
three fresh authority queries replace a former individual permission-rule read.

| Path | SELECT before → after | Writes |
| --- | ---: | --- |
| 2 / 100 Work Area changes | 14 → 16 | unchanged: 2 batched UPDATEs |
| FT Combo no-op | 11 → 13 | unchanged: zero |
| 2 / 100 hourly status changes | 12 → 14 | unchanged: 1 UPDATE |
| GET / Stage | 15 → 15 | unchanged: zero |

An ordinary assignment package adds share locks on **1 User, 1 app-access row,
2 permission rules**. It does not lock all users or all app-access records. The
global Staffing lock footprint documented above remains unchanged.

Focused PostgreSQL regression coverage includes cached permission poisoning,
committed access/role/rule/user/session/identity changes, zero partial writes,
real lock waits with revocation winning, successful Apply holding its authority
locks through commit, unrelated-user changes, and exact required-rule lock scope.
Existing Bulk Change atomicity, stale-package, FT Combo, Home lifecycle and query
budget tests remain in the validation set.

The authorization blocker is addressed. Narrowing workspace locks can resume as
a separate task, still requiring proof of every remaining organizational and
mutation invariant. Production behavior was not manually exercised or deployed.

Final focused validation: **59 passed, 3 skipped, 16 subtests passed** across
Bulk Change, SQL budgets, PostgreSQL final-authority/concurrency and normalized
Employee ID suites. The skips are PostgreSQL-only cases in the SQLite cost
fixture; their PostgreSQL counterparts ran. `compileall` and diff-check passed.
