# NeoRain issues 3–7: local presentation subset and release gates

Starting main: `ca386554608ecd3856438d1ebe952a26f06123cb`.
Repository: `Z:\DevProj\NeoApps`, main only. All five issue bodies and latest
amendments were read; issues 3–7 had no separate comments at inspection.

## Implemented subset (#4, #5, #6)

- Flat five-value Volume/Performance strip, separate Hub/Shift and Ramp staffing
  matrix, compact two-row lateness matrix with unchanged precision/calculations.
- Full available report width beside the persistent desktop navigation;
  existing NeoRain title geometry and dark/purple theme.
- Every inbound/outbound delay's minutes, code and full note appears by default,
  in saved order, including early and excluded flights. Briefing syntax unchanged.
- Aircraft-only live-board summary displays; canonical staffing services and
  Rainrock staffing remain intact.
- Independently collapsed Crew Admin with user/gateway-scoped browser preference.
  Existing forms/actions/permissions remain intact. Unsaved Crew Admin edits
  defer inbound revision-triggered reloads, instead of losing form input.

No schema changes, new Google writes, permission changes, or mission mutations.
Styles/scripts are inline server-rendered templates, so no stale static asset
URL needs replacing.

## #3 not implemented / mandatory safety work outstanding

The current `_raise_for_duplicate_operation_flight_number` in
`app/neomotherbrain/routes.py` compares uppercase raw strings, without normalized
identity or mutation-critical serialization. A local isolated fixture confirmed
that `UPS0948` and `UPS948` coexist in one operation and pass that guard.

New mission controls must not ship on that foundation. Use canonical shared
flight identity, protect creation and editing across both directions and all
relevant MotherBrain/manual/import paths, and prove simultaneous duplicate
submissions against disposable PostgreSQL. No disposable PostgreSQL URI/runtime
was available in this session; the Render connector listed no PostgreSQL instances.
Do not use production to run destructive concurrency fixtures.

Add Arrival/Departure, one-off editing, standalone/existing spares and HOT controls
still require implementation and authorization/current-sort/stale-write tests.
Existing canonical services are in `app/services/parking_plan.py`; forms and
mission mutation helpers are in `app/neomotherbrain/routes.py`. Do not silently
merge/delete existing normalized collisions or change recurring Master rows.

## #7 not implemented / Google safety boundary

Read-only live inspections confirmed:

| Field | Observation | Release status |
|---|---|---|
| STA | MotherBrain Inbound T4 array formula reads AB first; AB is override input | Candidate input; no safe staged write/propagation test |
| Block-In | MotherBrain U4 array formula reads AA; AA4/AA5 contain formulas referencing Reptile | Do not write U, AA or Rain G |
| eLMAC | Rain Outbound L3 array formula imports `simdata_load` column 13; Reptile Sim Load M has direct numeric inputs | Candidate upstream input; range identity/control/date contract not certified |
| Ramp/Crew LC, Block-Out | Rain Outbound M/N/O sample cells contain numeric time inputs | Candidate direct inputs; no staged write/readback test |
| Exclusion / No Return | Rain Outbound Q/S sample cells contain typed booleans | Preserve Q TRUE = excluded; no staged write/readback test |

Reptile Sim Unload T samples are direct time inputs upstream of MotherBrain AA,
but that alone does not certify a safe Block-In correction target or a complete
mission/date/row matching contract. No spreadsheet was changed.

The existing `app/services/google_rain_sheets.py` writer and outbound route remain
unchanged. They are NOT the requested durable Neo-first override architecture.
In particular, the old mirror route can still roll back Neo on a Google failure.
Do not claim override persistence, outbox retries, importer shielding, reset
semantics, cancellation of stale retries, or global no-blank-write guarantees
have been implemented by this presentation subset.

Before #7 implementation/release, certify exact writable upstream destinations,
named-range identities, gateway/sort/date matching and production credential
write permissions; use an isolated approved test workbook/range. Never write
formula/array-spill cells. Then implement per-mission/field explicit presence,
actor/version/audit, durable outbox with generation checks, fresh upstream
resolution on reset, and shielding in BOTH MotherBrain and Rain imports.

## Validation and deployment gates

Focused report/route/layout/delay suites and Chromium report/crew workflows cover
1440×900, 1920×1080, 390×844 and 375×568. Browser checks assert overflow, compact
matrices, visible ordered delay details, copy equality, Crew Admin persistence,
and preservation of unsaved input on revision refresh. Read-only historical
report/source-priority tests remain required.

Final subset results: 29 focused Python tests passed; one Chromium integration
test passed through all four viewport/workflow cases. Python compileall,
syntax checks for eight inline JavaScript blocks, and git diff-check passed.
The active mobile fixture also reproduced the read-only EXCLUDED label extending
past the viewport; its font now matches the existing compact inclusion control
and wraps safely. No values or inclusion semantics were changed.

This subset is committed locally only. Push/deployment is withheld because the
fresh independent operational-status gate could not be verified. Production
remains at the starting SHA; no new Render deployment was triggered.

Four old outbound test methods were independently run against original main
templates/tests in memory and reproduce three assertion failures plus two
subtest errors: cancelled-row attribute expectations (inbound/outbound), contact
popover count, and the old Include/Exclude header expectation. They are not
presentation-subset regressions; do not rewrite unrelated operational markup to
make those old expectations pass.

The production browser session redirected to Sign In. Relevant recent Render
GET/POST log filters returned no entries, but absence of logs is NOT proof of no
active sort. An independent authenticated operational-state check is still needed
before push. The user's initial no-running-sort confirmation is not replaced by
an invented later verification. No manual deployment is authorized.

## Copyable continuation prompt

Continue NeoRain issues #3–#7 in `Z:\DevProj\NeoApps` on main only. Read this audit
and all five GitHub issues, including #7's newest override-removal amendment.
Preserve the completed #4–#6 presentation subset, unrelated artwork, and stashes;
do not restart or reimplement it. Fetch/check origin before changes and every push.

Finish #3 with a shared normalized, transaction-safe duplicate-flight invariant
and PostgreSQL concurrency proof before exposing new mission/tail controls.
Finish #7 only after certifying writable upstream Block-In/eLMAC and other input
destinations with isolated tests. Implement durable Neo-first per-field overrides,
typed false versus unset, reset without blank writes, versioned retry cancellation,
honest sync state, and both import shields. Do not change recurring Master data.

Run focused Python/PostgreSQL/browser tests at all four requested sizes, including
midnight, permissions/history, duplicate edits/creates, reorder/mismatch/formula
protection, outages/retries/reset, report delays and briefing copy. Keep the old
outbound baseline failures separate. Reconfirm no active sort/significant live
activity before deployment; do not fabricate that check. Commit/push only a safe
coherent validated release, allow autoDeploy, and verify exact SHA LIVE and health.
Render workspace `tea-d79io9p4tr6s73cuk1bg`, service `srv-d8buq2mgvqtc73e375j0`.
Report every issue's true completion status and any remaining safety blockers.
# Latest continuation

The newer one-shot contract supersedes this audit's earlier retry/outbox proposal.
See `2026-10-14-neorain-release.md` for the implemented Neo authority/mission
controls, validation, and the explicitly withheld live Google writers.
