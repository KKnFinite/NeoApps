# N12: constant-query leadership aggregation

Starting main: `757836758aa2d493916b68224cd3200f50943cf8`.

`_board_leadership_index()` joined people to filter active employees but hydrated
assignments, then lazy-loaded each person's full record for classification. The
join did not populate the relationship. Cold sessions reproduced six SELECTs
for five leaders and fifty-one for fifty leaders.

The replacement selects only unit ID, classification and COUNT(assignment ID),
grouped by unit and classification. It preserves the nested dictionary contract,
both active flags, all database-valid classifications and assignment-row counting
(including database-valid legacy multiple scopes). No DISTINCT, additional
status/unit filters, employee fields, cache, schema or index changes are added.

## Isolated measurements

Median complete aggregation execution time across five runs with a cold ORM
identity map per run, SQLAlchemy cursor instrumentation, synthetic people and
assignments split across two units. PostgreSQL 17.11 runs locally on loopback
with a disposable schema per test, NullPool and 16 MiB shared buffers. Timings
include SQL execution, result transfer and Python/ORM work; they are not
production Neon latency or CU-hour estimates.

| Database | Leaders | SELECTs before / after | Median ms before / after |
| --- | ---: | ---: | ---: |
| SQLite | 0 | 1 / 1 | 0.156 / 0.243 |
| SQLite | 1 | 2 / 1 | 0.327 / 0.188 |
| SQLite | 5 | 6 / 1 | 0.908 / 0.225 |
| SQLite | 50 | 51 / 1 | 7.952 / 0.207 |
| PostgreSQL | 0 | 1 / 1 | 0.385 / 0.432 |
| PostgreSQL | 1 | 2 / 1 | 0.849 / 0.487 |
| PostgreSQL | 5 | 6 / 1 | 2.857 / 0.748 |
| PostgreSQL | 50 | 51 / 1 | 19.951 / 0.547 |

Every measured call produced identical results to the frozen original algorithm.
Both algorithms issued zero writes. The new helper hydrated zero employee or
assignment ORM objects. Timing variability and tiny-fixture overhead mean query
count, rather than a claimed speed ratio, is the durable regression guarantee.

## Validation

- Nine new SQLite and the same nine isolated PostgreSQL cases: 0/1/5/50 leaders,
  multiple units, active flags, unchanged employee-status semantics, all eleven
  database-valid classifications, duplicate assignment-row counts, reassignment,
  classification changes without caching, hierarchy counts and missing warnings.
- Eight existing SQLite regressions: required-headcount coverage and rollups,
  missing-leadership filters, assignment rules, denied board access, read-only
  controls and 20C hierarchy/affiliation safety.
- Two browser tests, Chromium and WebKit: authenticated Org Chart and People,
  legacy required-headcount redirect, five synthetic leaders, 1920x900 and
  390x844, Watcher read-only controls, no page JavaScript errors or page overflow,
  employee and assignment snapshots unchanged by viewing.
- Python compilation and `git diff --check`.

The original failing five-leader test reproduced `6 != 1` before the fix. All 28
final focused tests passed. Existing datetime.utcnow/Query.get deprecation and
SQLite fixture cleanup warnings were observed; no unrelated changes were made.

## Scope and production verification

Only the aggregation helper changes application behavior. Existing legacy
`dashboard_context()` calculations consume it, while current board routes use
Org Chart; required-headcount redirects there. This release does not establish
that every current board request runs this helper, or quantify production cost
savings. The current screens and protected legacy calculations are both tested.

No production employees, assignments, hierarchy, credentials or operational
records are changed by testing. No migration or hosting setting is required.
Render AutoDeploy and read-only production checks are reported in the delivery
response after the code is pushed.
