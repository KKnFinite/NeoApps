# NeoScorpion Truck Rolldown

Starting main: `0a25a58cd102437f9bd1069f226d77497ac5776c`.

## Scope and data ownership

Reports → Truck Rolldown is read-only, protected by the existing Fuel History
permission and the vendor route deny boundary. Select a sort/operation and one
truck or all trucks. COPY and CSV include summaries and chronological evidence.

Sources: nightly asset opening/current snapshots; append-only physical work IDs;
canonical posted FuelingEvents; inventory verification/discrepancy audits; truck
driver audits and manual-name audits; assignment milestones and resource-change
audits. ON/OFF and administrative finalization remain distinct. Historical
segment finalization does not assert replacement-aircraft mission completion.

There is no inventory replay or inferred running balance. Physical transfers
show their signed canonical change, except covered transfers show zero extra
deduction and discrepancies show an unknown/unverified movement. Historical
before/after balances appear only where actually recorded. Baseline adjustment
deltas are explicitly relative to the recorded baseline. Request states are
never interpreted as applied movements. Starting Gallons is not changed.

## Additive schema / existing TOP OFF actions

`neoscorpion_top_off_events` contains immutable SENT and RETURNED milestones.
Each RETURN references its SENT; a unique constraint allows one return per sent
event. Existing operation locks serialize concurrent actions. ORM guards and
PostgreSQL/SQLite triggers reject edits/deletion. Standard schema registries
create the table idempotently. No existing rows, IDs, events or balances migrate.

SENT captures timestamp, actor, driver identity, recorded gallons, confidence and
inventory epoch. An uncovered OFF transfer makes the departure snapshot
uncertain even if a dispatcher recently verified a pre-transfer baseline.
RETURN captures its genuine timestamp and newly confirmed gallons. Multiple
trips stay independent. An already-in-progress legacy trip can gain a future
RETURN but no fabricated SENT snapshot. Existing last-sent times and confirmed
legacy return audits remain visible with explicit limitations.

## Historical limitations

- No invented parking, driver, travel or OOS intervals. Driver history is resolved
  only from identity changes at or before each event, not today's assignment.
- Flight/destination identify the linked canonical mission. They are not claimed
  to be immutable location snapshots. Physical tail, truck, Fueler and T/F come
  from the actual event/segment, including A→B→A and swaps.
- Earlier TOP OFF trips may lack boundaries or verified departure inventory.
  Known counts/additions are labeled as recorded/partial rather than complete
  historical totals. OOS duration remains unavailable without paired boundaries.
- Per-source 10,000-record limits explicitly mark unusually large reports partial.
- The operation chooser follows the existing report pattern (200 recent sorts);
  gateway-validated operation IDs can also select older sorts directly.

## Validation

- 12 focused report tests: canonical transfers; OFF vs COMPLETE; epoch-covered
  transfer; baseline correction before unposted T/F; pending exclusion; fleet;
  A→B→A; truck swaps; driver chronology; historical isolation; access and CSV;
  multiple/legacy TOP OFF trips; rollback and immutable records; uncertainty.
- 2 disposable PostgreSQL tests: concurrent SENT/RETURN uniqueness, repeat
  additive bootstrap, database immutability, preservation of existing physical
  event IDs/times/gallons and exactly-once inventory behavior.
- Chromium and WebKit: authenticated report navigation, historical/individual
  selection, COPY exact text, CSV, read-only controls, no page errors or overflow
  at 1440×900, 1920×1080, 390×844 and 375×568.
- Python compileall, shared copy-script syntax, git diff-check.

An initial unittest module command accidentally discovered imported legacy
fixture classes (59 tests, 6 failures / 3 errors). Those legacy classes contain
old auto-completion expectations and are not changed by this report. Subsequent
commands select the new report classes explicitly. No broad-suite success is
claimed; the focused gates above are the release evidence.

Protected NeoRain prototype files and artwork are excluded from this release.
No PROF/SPEAR, Dispatch layout, fueling calculations or physical milestone code
is changed. Only the existing TOP OFF actions gain operational persistence.

## 2026-10-21 — distinguish sent assignments from planning changes

Starting main: `327cb218fdfbc87c7a41c0fa27d73e7265f0255f`.
Read-only production evidence confirms the shared `swap_truck` action also
records initial assignments (`old_value = NULL`): audit 953 is an initial
assignment, while 971 changes truck 4 to 7 after physical work on truck 4.
The old report blindly rendered both as SWAP TRUCK and could also attribute an
audit to a third truck currently attached to the linked mutable work record.

The report now resolves evidence strictly at the change timestamp, within the
assignment and recorded cycle. A noninitial change needs a preceding committed
Dispatcher ASSIGN/UPDATE receipt for the old truck, or real preceding physical
ON evidence. Later ON, dropdown selections, fuel autosaves, pending-plan edits
and today's resource labels cannot establish an earlier sent assignment. An
intervening resource change breaks the receipt chain; A→B→A does not borrow an
old A receipt. Physical work still in progress remains evidence independently
of intervening planning edits. Conflicting simultaneous physical identities
are not resolved by guessing.

Initial committed assignments retain MISSION ASSIGNED entries, not SWAP labels.
Proven reassignments appear for their actual old/new trucks only. A proven
removal is UNASSIGN TRUCK. Initial/planning/unknown swap-shaped raw audits stay
available in a collapsed audit-evidence section, outside the operational
timeline and exports. Timeline, COPY and CSV share one filtered event collection.
No operational or historical records are changed; no schema migration.

Validation: 20 report tests, 12 assignment-receipt tests, 5 focused disposable
PostgreSQL filtering/TOP OFF concurrency tests, 2 JavaScript COPY checks, and
authenticated Chromium/WebKit report workflows at 1920/1440/390/375px. Browser
checks include individual/all trucks, raw evidence access, chronological source
identity, COPY and CSV agreement, historical/partial data and mobile overflow.
Compilation and diff checks also pass. The older PostgreSQL bootstrap-history
test has a baseline failure before report generation: it invokes Dispatcher
COMPLETE without mandatory Fueler submission. It is not part of the passing
five-test PostgreSQL gate and was not changed to weaken production validation.
