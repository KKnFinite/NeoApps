# NeoStaffing normalized Employee ID integrity

Base: `b4d7f1c328e10031b631be592ac3642f22f3058b`.

## Contract and preflight

Bulk Change's existing comparison is Python `employee_id.strip().lower()`.
Person create/update already require nonblank input and trim it without changing
display casing. The column remains NOT NULL; no new nullable/blank policy, CHECK,
data rewrite or normalized-ID column is introduced. Legacy NULL/blank records
block this upgrade for explicit review rather than being silently repaired.

The same disposable PostgreSQL fixture as the Bulk Change audit contains 119
people and 112 assignments. Preflight found **zero normalized collisions and zero
NULL/blank IDs**. This is fixture evidence, not an audit of the live production
database. Deployment audits its own data before installing enforcement.

`audit_employee_ids()` groups existing IDs with the exact Python normalization and
reports every colliding person ID/stored Employee ID. Collision or invalid blank
data raises an error before creating either schema object. Operators must review
the identified employees, confirm their real distinct IDs and correct records
explicitly; bootstrap never merges, renames, deletes or chooses a winner.

## Database enforcement and deployment

The approved explicit `scripts/bootstrap_database.py` / `sync_database_schema`
pipeline adds:

- Immutable, strict, locale-independent `staffing_employee_id_key(text)`.
- Unique expression index `uq_staffing_people_employee_id_normalized` on that
  key with bytewise `C` collation.

The prior exact-text unique index remains. `db.create_all()` alone does not
upgrade an existing database; the explicit pre-deploy bootstrap is required.
SQLite development behavior is unchanged. Worker/request GETs do not run this
upgrade.

Simple PostgreSQL `lower(trim(...))` is insufficient: trim omits Python whitespace
such as tabs/NBSP, and lowercase depends on server locale/Unicode data. Local
PostgreSQL's ICU lowercase missed 40 of Python's 1,433 lowercase-changing scalar
characters. The SQL function instead embeds character mappings generated from
the application's Python runtime, including expanding lowercase and contextual
Greek Final Sigma. It uses only string operations, not queries, ICU, extensions
or mutable application data. Generated code is memoized only during bootstrap.

Bootstrap takes a deployment-only SHARE ROW EXCLUSIVE table lock with a five-
second lock timeout, audits, then creates the function/index in one transaction.
Writers cannot enter the audit-to-index gap; ordinary readers may continue. The
caller owns commit/rollback. Existing function and index definitions are verified
on repeat execution, not silently replaced. A Python Unicode change that changes
the generated function requires an explicit audited index migration; replacing
an immutable function beneath an existing index would be unsafe. Bootstrap fails
closed in that situation. No manual production DDL is required for the normal
upgrade; a collision or definition mismatch needs operator review before retry.

Concurrent duplicate INSERTs/UPDATEs receive PostgreSQL 23505. The existing
safe mutation-error boundary maps this specific index to `Employee ID already
exists.` without exposing SQL, parameters or other people's details.

## Verification

Focused PostgreSQL tests use only generated schemas in a disposable loopback
`neostaffing_test_*` database:

- Existing 119-person fixture and empty schema upgrade; repeat sync without data
  changes or duplicate indexes.
- Collision preflight (case and whitespace), NULL/blank fail-closed review.
- Case/space/tab/Unicode-space duplicates; Unicode case expansion/Final Sigma;
  distinct IDs and original stored casing/spacing preserved.
- SQL normalization compared to Python over the complete Unicode scalar corpus
  plus contextual Sigma and whitespace probes.
- Competing INSERT and UPDATE transactions: one commit, one unique violation
  for case-only and whitespace duplicates; rollback leaves one canonical key.
- Writer blocked while migration is uncommitted; duplicate rejected after commit.
- Real People create/update HTTP paths return usable duplicate messages.
- Unexpected function/index definition refused rather than silently replaced.
- Bulk Change 2/100-person query/write budgets stay **14 SELECTs, 2 batched
  UPDATEs, 0 INSERTs**. No runtime reads or application writes were added; the
  additional cost is computing/storing an index key on Employee ID writes.

Run with `NEOSTAFFING_TEST_POSTGRES_URL` pointing at the disposable database:

```text
python -m pytest -q tests/test_neostaffing_employee_id_schema.py tests/test_neostaffing_bulk_change.py tests/test_neostaffing_bulk_change_cost.py tests/test_database_bootstrap.py
```

Recorded validation: focused schema/Bulk Change/bootstrap run **58 passed,
3 PostgreSQL-only skips**; final PostgreSQL run (including the additional
exact-duplicate error regression, existing Bulk Change and Shift concurrency)
**29 passed, 11 unrelated tests deselected**. No failures. Existing SQLAlchemy
legacy/deprecation warnings remain. `compileall` and `git diff --check` passed.

## Bulk Change follow-up

The normalized-identity invariant now rejects the previously reproduced
`NEW-ID` / `new-id` concurrent-write counterexample at the database boundary.
That blocker is addressed once production bootstrap succeeds. **No Bulk Change
locks are narrowed here.** Its global workspace lock footprint remains as
documented in the earlier audit. A later narrow-lock change must still prove
its other scope, revision, relationship, assignment and topology invariants.

No production operational data was modified or production deployment manually
triggered for these tests.
