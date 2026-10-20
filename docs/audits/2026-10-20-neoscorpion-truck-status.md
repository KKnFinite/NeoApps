# Truck activity, travel wording and optional inventory audit

Starting main: `1ce9bbe5bde749d31e9ae6c062788dd857e6bde9`.

## Reproduced causes and corrections

- `_fuel_start_records()` treated an assignment's administrative completion as
  physical release even with an open, trusted ON. The starting code reproduced
  `IDLE · LAST B06` for that evidence. A visit's open ON now survives administrative
  completion; only its physical OFF/End Early releases it. SPEAR also retains
  physical ownership before applying administrative-completion filters.
- `_fuel_rows()` replaced a false-valued `WorkIndex` with `{}`. A legacy tail
  handoff can have no current-tail entry while retaining a confirmed physical
  visit. Preserve its retained segments and the canonical parent assignment in
  the tail-mismatch projection. No ON/OFF or historical records are rewritten.
- Release matching used assignment identity, conflating separate visits of the
  same assignment. The existing bounded event query now includes work ID; start
  gates compare physical visit IDs when available, retaining legacy fallback.
  Prior visit releases inform travel without releasing the current visit.
- `ARRIVAL UNCONFIRMED` came from expiry of predicted **truck** travel, unrelated
  to aircraft arrival. Canonical reason is now `TRUCK ON PENDING`; compact rows
  show `TRUCK WAIT`, with explanatory title. Server and client agree. A trusted
  ON clears travel metadata, so the clock cannot restore a stale warning.
- Ordinary OFF deliberately sets `inventory_verified=False` and retains the
  pending inventory epoch until canonical T/F posting. Dispatch incorrectly
  classified every false flag as mandatory verification. It now excludes this
  normal pending reconciliation from Action Required. Unknown baselines and
  discrepancies still require reconciliation; submitted material reading
  discrepancies still require approval. Optional physical readings remain
  available. No inventory/submission/completion mutation was changed.
- SPEAR's inventory eligibility is unchanged: unverified gallons cannot fund a
  safe recommendation. Both planner explanations now distinguish this inventory
  constraint from physical truck unavailability. DEFUEL capacity, sump, TOP OFF
  return, posting and epoch safeguards remain intact.

## Evidence and checks

- Five new integrated regressions cover retained visits, administrative versus
  physical release, same-assignment previous visits/travel, optional reading
  skipped through OFF/submission/completion, and unknown inventory.
- Focused Python group: 80 passing tests (new audit, Fueler submission,
  continuations, whole-sort planner, Dispatch Details). Final five-test audit
  rerun also passed. Two additional DEFUEL recovery tests and two current TOP OFF
  accounting tests passed. Focused JavaScript group: 17 passing tests.
- Compilation, JavaScript syntax and diff whitespace checks passed.
- Isolated in-memory Chromium browser fixture: previous truck OFF at A08 with
  current ON at B06; normal Dispatch and Board View; arrived aircraft with truck
  travel pending; live ON removes warning; live OFF updates truck activity;
  unsaved Tail Notes, focus and expanded Details survive. Mobile Fueler submission
  succeeded without an optional reading, followed by Dispatcher completion and
  a single inventory deduction. Desktop/mobile geometry inspected, no page
  overflow; compact travel secondary line measured as one line.

## Production scope and limitations

Authenticated production inspection found truck 422809 and N121UP/UPS1076 already
completed; 422809 was subsequently TOPPING OFF. The original erroneous instant
could not be observed retrospectively. Neon connector database inspection was
unavailable (`list_projects` not found), and this laptop has no DATABASE_URL.
The defects above are demonstrated code-level causes with production-shaped
isolated evidence; the exact historical database sequence for the reported
instant remains unverified. No production fueling transactions were modified.

An expanded legacy regression selection had 29 existing failure/error cases,
all reproduced against untouched starting-main sources. They include obsolete
automatic-ON, pre-submission completion, forecast and source-template assumptions.
An additional older TOP OFF assertion expects RETURN to rewrite starting gallons;
it also fails unchanged on starting main. Current targeted safety/accounting
checks pass. PostgreSQL integration was not run without a configured test DB.

No schema change, extra polling timer or new database query path was added.
