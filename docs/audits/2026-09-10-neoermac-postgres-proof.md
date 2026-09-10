# NeoErmac PostgreSQL concurrency and query proof

Starting main: `20b17dded0125f18d120e380de41c5132cc3a8d5`.

## Environment and limits

PostgreSQL 17.11, local Windows loopback, READ COMMITTED, independent connections
and isolated disposable schemas. All records are synthetic. No production
database, credentials, traffic, migrations, or deployment were used. Tables and
indexes come from the current SQLAlchemy metadata; this does not attest that a
particular production database has applied every declared index.

## Reproduced races

| Concurrent operation | Before | After |
| --- | --- | --- |
| First canonical Door Pull, independent PURE/MIX | Both succeed; one canonical row, both values retained | Unchanged/pass |
| Existing pull, same field from same original | One success, one 409; winner retained in aggregate | Unchanged/pass |
| D1/D4 contributing to one mission | Both succeed; latest actual time aggregated correctly | Unchanged/pass |
| Existing ULD request, simultaneous increments | Both return success, but A2/A1/AMP end at 2/4/6 instead of 3/6/9 | 3/6/9; no lost increments |
| Absent ULD request, simultaneous first increments | Existing requester/sort/door uniqueness constraint raises UniqueViolation | Both succeed; one row with 2/4/6 |

The ULD failure was forced by allowing both real transactions to complete the
same pre-update lookup before either wrote. It is not inferred from SQLite.
An increment-only SQL-expression trial fixed the existing-row race but not the
absent-row race, and was replaced rather than retained alongside the final fix.

`app/services/uld_requests.py` now acquires the established Gateway row lock
before lookup/create/increment and refreshes any already-loaded request instance
with `populate_existing`. This protects missing rows and stale ORM identity maps
in one transaction. The caller still owns commit/rollback. No retries, additional
constraints, schema changes, or GET locks/writes were added. Existing permissions,
requester/door/sort selection, setup/respot separation and timestamps are unchanged.
The cost is one additional lock SELECT per PostgreSQL increment, not another
production UPDATE. SQLite uses the existing local-only no-op Gateway UPDATE
reservation convention. This proof covers increment/increment contention, not
every possible concurrent edit/delete/fulfillment combination.

## Actual poll query plans

The test captures the application's executed SQL and bind parameters and runs
`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` on every SELECT for all four changed and
unchanged state endpoints. Raw evidence is local/ignored:
`instance/neoermac-postgres-proof/plans.json`.

Fixture: 50 current departure missions and master choices, 100 previous sorts,
7,500 historical missions (including 2,500 arrivals), and 5,000 historical rows
each of Door Pulls, parking, Google mission links, ULD requests and send events.
Timeline configuration is populated, not an absent-row default.

| Endpoint | Unchanged SELECTs | Changed SELECTs | GET writes / commits |
| --- | ---: | ---: | ---: |
| Door View | 11 | 20 | 0 / 0 |
| Building Lineup | 9 | 12 | 0 / 0 |
| Upcoming Pulls | 9 | 13 | 0 / 0 |
| View Outbound | 9 | 15 | 0 / 0 |

Existing operation indexes scope the large history tables to current-sort rows.
The regression checks reject full sequential scans of these populated history
tables and require each scan to return at most 100 rows. This is a controlled
fixture budget, not a cap on valid production mission counts.

Remaining sequential scans are small: configuration/access tables, 12 current
lineup rows, 50 relevant master choices, and the 101-row operation table (two
buffer pages, returning current/prior-date candidates). The operation date index
already exists. Reading the full relevant master-choice/current-lineup set is
necessary for their revision dependencies.

No index was added or changed. Composite indexes duplicating already selective
operation lookups and covering indexes including frequently written timestamps
are not justified by these plans; they would add storage/write maintenance
without a demonstrated useful benefit. There is no before/after index-tuning
claim: poll SQL and schema are unchanged by this fix. Warm local revision
execution times are sub-millisecond, not estimates of Neon CU savings or remote
request latency. Recheck against real production statistics if distributions or
current-sort sizes differ substantially.

## Reproduction and validation

Create an empty local PostgreSQL database named `neoermac_test_*`, then set
`NEOERMAC_TEST_POSTGRES_URL` to that loopback database URL and run:

```text
python -m unittest tests.test_neoermac_postgres -v
```

The suite refuses non-loopback/non-test database URLs, does not consume
`DATABASE_URL`, creates UUID-named schemas and drops only those schemas. It is
opt-in when no disposable PostgreSQL URL is supplied. Concurrent requests use
different PostgreSQL backend PIDs. The ULD regression deliberately preloads both
old ORM snapshots before acquiring the real lock.

Validation: 41 focused tests passed, including six real PostgreSQL tests, existing
pull integrity/aggregation tests, Ermac ULD access/scope/edit/delete/state tests,
and affected Sektor Discharge send/remaining-count tests. `compileall -q app` and
`git diff --check` passed. No JavaScript changed.
