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
