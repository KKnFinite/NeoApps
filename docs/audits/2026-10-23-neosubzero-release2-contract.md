# NeoSubZero Release 2 operational contract

Starting main: `a6a613213e7d75b1346a2c6ef55c0507b88b0e8b`.

## Confirmed existing paths and defects

| Concern | Existing path | Confirmed limitation |
| --- | --- | --- |
| Effective tail | MotherBrain `tail_swap_mission`, Google `_apply_effective_tail` | Canonical mission tail changes; SubZero must consume it without moving physical history. |
| Pretreat | `services.mutate_pretreat`, operation/tail unique state | Already tail-owned, but permanent fluid/crew evidence is not captured at END. |
| Departure | `mutate_departure_deice`, unique mission event | Mission lookup and uniqueness prevent independent replacement-tail work. Reset actions clear previous treatment fields. |
| CLEAR | departure mutation + shared template | Allowed only after FIN; negative/no-spray outcomes are incorrectly terminal before explicit clearance. |
| Participation | `set_neosubzero_spray_gallons` | Snapshot occurs at gallon entry, using then-current DUCC staffing, not at pass END. Blank gallons deletes evidence. |
| Driver access | Deicer route and `_can_edit_position` | Current ramp/position controls ownership; historical participation after reassignment is inaccessible. Driver truck editing is currently exposed. |
| Shared team | DUCC assignment/truck services | Reuse existing qualification, uniqueness, operation locks, version and move/swap services. |
| Application Context | `_application_context`, Flask session | Not shared between operators and not version protected. |
| Reporting | `neosubzero_deice_log` | Tail/flight are read from mutable mission rather than original physical evidence. |

## Implementation invariants

- Existing departure events and spray records remain the canonical treatment system.
- Event `tail_number` is physical ownership. Original mission FK is retained as provenance, never rebound on swap. New events snapshot original flight context.
- Resolve current work by operation/effective tail. Multiple unresolved active legacy events require review, never an arbitrary merge.
- Relax mission uniqueness without deleting events. Preserve legacy IDs, foreign keys and values during repeatable PostgreSQL/SQLite upgrades.
- Extend existing spray records to represent planned/official per-pass participation independently of gallons. Four slots maximum; no aircraft-level gallon totals.
- Pass END captures fluid, surface, shared timing, weather and selected truck/crew atomically. Pass 2 is independent. Later DUCC changes do not update official snapshots.
- Removing participation never deletes positive applied gallons. Explicit reporting corrections retain before/after audit evidence and do not reset FIN/CLEAR.
- Unsprayed CLEAR creates only an operational clearance/outcome record: no spray, FIN, gallons or invented physical timestamp.
- Actual clearance action time is recorded separately; legacy missing clearance times remain missing. Cleared aircraft sort last, newest verified clearance first.
- Driver authorization follows their documented official pass participation, not their present ramp. Driver access is read-only except own-truck gallons.
- Shared personnel/truck controls mutate only existing DUCC assignments under existing safeguards; aircraft participation edits do not mutate DUCC.
- Application Context becomes one versioned current-operation record, with historical values frozen into pass evidence.
- All operational mutations lock the canonical operation, validate effective tail/scope/version, preserve drafts on conflicts, and publish through existing revisions.
- No production record repair, historical backfill guesses, destructive cleanup or separate polling framework.

## Verification gates

Reproduce original failures first; add service/PostgreSQL/browser cases for swaps, unsprayed CLEAR, independent END snapshots, corrections, driver reassignment, permissions, concurrency and populated old-schema upgrades. Run Release 1 regressions and actual authenticated production route/asset/log checks after exact-SHA AutoDeploy.

## Implemented boundaries and recovery

- Canonical schema registries include all added columns, shared operation context and append-only audit tables. Repeatable constraint relaxation removes only mission-only event uniqueness and permits participants without invented gallons/times. A one-parent check protects Pretreat/departure identity, and per-parent/pass/slot uniqueness remains enforced.
- PostgreSQL upgrades use the existing deployment bootstrap and advisory locking. SQLite performs a transactional, FK-checked copy only where constraint relaxation is necessary. Populated legacy IDs, values, timestamps and references are tested for preservation.
- Aircraft controls distinguish selected participants from official END snapshots. ADD/REMOVE never changes DUCC staffing. Shared CHANGE TRUCK/CHANGE DEICERS use DUCC qualification, uniqueness and version safeguards. Passes may have different trucks and crews.
- Participant corrections after FIN/CLEAR require an explicit reason and actual-crew confirmation. Removing a contribution with positive gallons retains it. Blank gallons cannot delete applied fluid evidence.
- Current-sort Driver gallons are authorized by documented Driver identity, including after reassignment. Driver truck/staffing/treatment/CLEAR mutations remain prohibited.
- New mutations reject stale operation, tail, parent, participant or staffing tokens. Drafts retain their original tokens; current-value reload is explicit and discardable. No automatic conflicting retries.
- Reports and readbacks use recorded product evidence, not later Settings values. Missing historical product/flight/crew/weather evidence remains identified as missing. Legacy actual timestamps are never reconstructed.
- A genuine multi-event ownership ambiguity is a review condition, not permission to merge or delete records. Production preflight found no such conflicts.

## Evidence and regression notes

The original three service failures were reproduced before implementation: mission-linked tail ownership, unsprayed CLEAR rejection and missing END-time participants when gallons were absent.

Isolated browser evidence is under `instance/browser-evidence/subzero-release2/` and `instance/browser-evidence/subzero-operations/`. Chromium/WebKit cover 320, 375, 430, 768, 1024, 1440, 1920 and 2560 px, actual participation/team/CLEAR/correction forms, Driver reassignment and stale-save recovery, shared live updates and Release 1 drafts/preferences.

Three broader failures were independently reproduced against archived unchanged starting main: the two NeoStaffing attendance-authority fixture failures and the NeoRain/calibration redundant-CREATE bootstrap mock assertion. They are not repaired by changing unrelated applications in this release. DUCC's broad browser run exhibited a timing failure; its complete physical-board/staffing/TV workflow passed on isolated rerun, while unchanged main also exhibited a browser timing failure.

Production preflight was read-only: one historical planned departure event (ID 1, operation 61), no actual pass timestamps, no spray records, no Pretreat states, and no ambiguous active-tail events. Its original fields and update time must remain unchanged after bootstrap. The authenticated production Operations screen reported no current sort. No operational test data or historical repair is authorized or needed.
