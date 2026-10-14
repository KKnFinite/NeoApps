# NeoRain #3–#7: validated independent release

## Scope and authority

Local starting HEAD: a5ba161cae58603383490e33c9a45ba17e0717f8.
Upstream at start and final inspection: ca386554608ecd3856438d1ebe952a26f06123cb.
The completed #4–#6 commit is preserved, not rebuilt. The user reconfirmed no
current operational sort or significant shared-node use before release.

* #3: Rain current-sort arrival/departure create/edit and HOT/spare controls reuse
  MotherBrain's canonical mission, parking, tail and crew services. Existing
  Simulator-level mission-management edit permission remains unchanged.
* #4–#6: preserved compact KPIs, separate Hub/Ramp matrix, lateness matrix,
  visible ordered delays, flat purple reports, aircraft-only live summaries,
  persistent collapsible Crew Admin and briefing copy functionality.
* #7: Neo field authority and UI implemented; live Google writeback withheld.

## Persistent Neo overrides

New `neorain_mission_overrides` and `neorain_override_audits` tables contain
explicit presence, field value/source, actor/time, generation, upstream snapshot
and sync disposition. Canonical mission fields remain the operational facts.
No separate mission or calculation model was introduced.

STA and ETD accept exactly four digits and autosave only valid HHMM. STA is
distinct from ETA and supplies the arrival lateness baseline. ETD changes ETA,
not STD; departure lateness retains STD. Existing overnight resolution is reused.
Block-In, eLMAC, Ramp LC, Crew LC, Block-Out, inclusion and No Return have explicit
per-field authority and reset. No Return remains typed Boolean and requires the
existing milestones; reversal requires confirmation and preserves timestamps.
Other-authority finalized departures cannot be unsafely reversed.

Neo commits before any Google attempt. Blank/untouched/reset never writes Google.
An edit has at most one attempt; no scheduler, outbox, automatic retries or replay.
Removing an override restores current matching upstream where a Google link
exists; errors are disclosed and ordinary imports resume without blank writes.
Neo-only missions perform no Google I/O and do not create Google flights. Adding
a Google link later does not replay earlier Neo edits.

Import adapters explicitly capture upstream facts and a mission-lock-protected
flush guard prevents concurrent stale imports from replacing active overrides.
Non-overridden fields continue normal import updates. Unrelated fuel/state
mutations do not incur override-lock queries.

## PostgreSQL bootstrap

The two metadata/audit tables are additive and idempotent. A shared normalized
flight key treats UPS0948, UPS948 and 5X0948 alike. A PostgreSQL trigger serializes
on the parent operation and rejects conflicting creates/identity edits across
arrival/departure, including raw SQL and repeatable-read concurrent writers.
Different operations remain independent. Existing historical collisions are not
merged, deleted or rewritten; non-identity updates remain possible. No fueling,
inventory, Master Schedule or historical relationship data is migrated.

## Google certification: explicitly incomplete

Approved ranges are registered exactly, including STA AB4:AB50 (not AB100).
ETD has no writer or registered destination.

Read-only connected inspections confirmed:

* MotherBrain Inbound U4 and Rain Inbound G3 are array-formula-owned and forbidden.
* Rain Outbound L3 imports Reptile `simdata_load`, column 13; L is forbidden.
* Sample Reptile Sim Unload T4:T8 and Sim Load M4:M8 contain direct time constants.
* Sample Rain M/N/O3:6 contain direct time constants. Q/S3:6 contain typed Boolean
  constants; Q TRUE means excluded.
* MotherBrain AB3 says ENTER DAY AND TIME; sampled AB4:6 are blank, not certified
  by that observation alone.
* Drive permission metadata lists the named Sheets service account as writer on
  all three books. This does not prove production credential/scopes or effective
  write permission/protections for a destination.

The available connector does not expose sufficient named-range/protected-range
and array-spill contract evidence. Current operational date/identity mappings and
Reptile Block-In propagation are not fully certified. No production service-account
write or isolated safe workbook/range write was performed. Therefore ALL eight
live writers remain disabled, reporting NOT_WRITTEN honestly. The tested adapter
interface enforces exact identity, date, route/tail, approved bounds, formula/spill
checks, immediate recheck, typed values and readback; no adapter is configured by
this release. Do not describe this as working production synchronization.

No formula, spreadsheet value or spreadsheet permission was changed.
No safe manual ETD destination is certified, so no speculative approval range is
proposed. Investigate and present an exact verified range before any ETD writer.

## Validation

* 38 focused Python tests: mission/tail routes, permissions, stale/current-sort
  checks, authority, reset, No Return, Google-failure/no-retry safety, identity/
  formula/spill/range checks, reports/layout/delay information.
* 19 disposable loopback PostgreSQL tests: full override contracts, concurrent
  same-version edits, additive bootstrap twice, raw normalized-flight inserts/
  edits, cross-sort independence, concurrent repeatable-read identity changes,
  historical collisions and relationships preserved.
* Chromium integrated workflow at 1440×900, 1920×1080, 390×844, 375×568: STA
  0000/0235/2359, invalid/incomplete entries, reset, ETD, actual mission creation,
  report matrices/delays, clipboard equality, Crew Admin persistence/dirty refresh
  preservation, no page horizontal overflow or JavaScript errors.
* compileall, shared JS syntax and diff-check.

Previously documented old outbound assertions reproduce on original production
main (cancelled attributes, contact popover count, old inclusion heading). They
are not certified against the new authority contract and were not broadly debugged.
No artificial production operational transaction is used for verification.

## Copyable continuation prompt

Continue NeoRain #7 Google certification in Z:\DevProj\NeoApps, main only.
Fetch latest origin/main; preserve shipped #3–#6 and the Neo override implementation,
unrelated artwork and protected stashes. Read this audit and latest issues #3–#7.
Do not rebuild completed UI, missions, PostgreSQL uniqueness or authority.

Finish a real bounded service-account adapter ONLY after certifying exact current
sort/date and normalized flight/direction/tail/route identities, all approved input
ownership/protections, service-account scopes/permission, Reptile propagation and
an isolated safe write test. Register only individually certified fields. User's
approved destinations: MB Inbound AB4:AB50; Reptile Sim Unload T4:T53 and Sim Load
M4:M53; Rain Outbound M/N/O/Q/S3:50. Never write formula or array-spill cells.
Q TRUE means excluded; booleans must remain typed.

One Google attempt per new intentional edit, never retries/reassertions. Neo saves
first and survives failures. Blanks, untouched, reset and deleted override produce
zero writes. Neo-only missions do no Google I/O. Later Google matches never replay
old edits. ETD is Neo-only: investigate and request approval for an exact verified
manual range before coding its writer.

Run focused fixtures and disposable PostgreSQL/browser safety tests, reconfirm no
active operational use before push, commit main, allow autoDeploy (never manual),
and verify exact SHA LIVE and health. Report certified versus withheld fields
honestly. Do not test destructive writes against operational flights.
