# Change Requests read-path proof

Base: `5a4a1044460e27abd1dd5169fc59619316b46a8a`.

Measured fresh authenticated HTTP GETs to `/neostaffing/requests`, including
access, existing scoped maintenance, notification/navigation context and template
rendering. Fixture: 205 fresh pending requests, one item each, 211 people, 206
active work assignments, five leadership assignments and six hierarchy units.
SQLite and disposable local PostgreSQL 17 produced the same read counts.
PostgreSQL baseline was measured by loading the base service from Git into the
same test harness, without changing the checkout. No production DB was used.

| View | Reads before | Reads after |
| --- | ---: | ---: |
| Department supervisor, Purview | 22 | 21 |
| Operation manager, Purview | 22 | 21 |
| Division Manager, Purview | 20 | 19 |
| No management identity, Purview | 20 | 15 |
| PT supervisor, All | 20 | 19 |
| FT supervisor, default Routed | 23 | 23 |

Reads count both SELECT and read-only WITH statements. These fixture GETs execute
zero INSERT/UPDATE/DELETE statements. Existing expiry/notification maintenance
can still write when there is actual maintenance to perform; it is unchanged.

## Confirmed inefficiency and fix

Previously every view hydrated all 205 requests/items, all 211 people, all 206
assignments and all five leaders before filtering in Python—even empty Purview.
Query count was flat, but transferred/hydrated rows and rendered drawers scaled
with the entire queue.

The load now applies Purview/unassigned restrictions before detail hydration,
reuses the request's management identity, loads only its leadership/20C union,
and selects submission candidates using the existing active-assignment rules in
SQL. Only a selected candidate's assignment rows are needed. Existing globally
available destination choices are retained.

Request detail pages contain at most 100 requests. SQL pagination uses one extra
row to detect Next, without a total-pages query. On the fixture, normal pages
hydrate 101 request rows (including the peek), 100 items, 101–102 people for
management, one leader and zero assignment rows. PT All loads 156 people because
its authorized submission picker remains available. Empty Purview hydrates no
people, requests, items or leadership rows. Existing pagination styles are reused.

## Semantics and remaining work

- All remains the existing All visibility, including for users without a linked
  management identity who have page access. This pass does not redefine that
  authorization contract. Purview remains empty without leadership authority.
- Source OR destination ancestry, multiple leadership assignments, active 20C
  affiliations, routed IDs, inactive ancestry, history windows and ordering are
  preserved. Approval/write services are untouched.
- Unassigned badge remains view-wide before queue/search filtering.
- Routed legacy JSON and Unicode substring search retain exact Python matching.
  They stream scalar projections in batches of 100 and stop after the requested
  page plus one match. Sparse searches/deep pages can still scan many scalar
  rows; no unsafe textual JSON approximation or cross-request cache was added.
- All hierarchy units remain necessary for existing destination choices/paths.
  Authorized submission pickers are not paginated in this change.
- Existing scoped notification/retention maintenance is unchanged, including
  its separate routed-recipient matching work. No new maintenance or polling.

## Regression evidence

`tests/test_neostaffing_change_request_reads.py` covers the real route budgets,
hydration bounds, zero writes on the fixture, one-versus-205 request query-count
equivalence, page navigation/details, pending/history ordering and badge behavior.
An oracle using the original visibility helpers compares complete paginated sets
for Department, Operation, Sort, no-scope, union and 20C scopes; it also exercises
cross-boundary destination ownership, legacy routing encodings, literal wildcard
search and all existing queue modes (60 subcases).

The focused Change Requests, notification and 20C suites also remain required,
including the existing 1,500-person query budget. No budget was relaxed. No
schema, index, approval semantics, assignment behavior or frontend JS changed.
