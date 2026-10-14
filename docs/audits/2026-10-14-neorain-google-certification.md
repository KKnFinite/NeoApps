# NeoRain #7 certification continuation — BLOCKED, no writers enabled

Credential-access prerequisite correction: new ZEUS secrets and Render SSH are
NOT required. The continuation in `2026-10-14-neorain-runtime-adapter.md` reuses
the established production connection through a Grandmaster-only diagnostic.
The historical access notes below describe the earlier attempt, not a new
credential requirement.

Starting local/remote/production: 2c05915a78754fa51de4742597227471a50ed61b.
Latest issue #7 and its comment were read completely. Range approvals and the
one-shot contract remain unchanged. No completed #3–#6 functionality was rebuilt.

## New read-only evidence

All approved ranges were read with userEnteredValue, effectiveValue and validation:

| Field | Approved range | Observation | Release status |
|---|---|---|---|
| STA | MotherBrain Inbound AB4:AB50 | Entire range blank; T4 array formula consumes AB first | BLOCKED: blank/spill contract and production credential proof missing |
| Block-In | Reptile Sim Unload T4:T53 | 26 direct time constants, no formula anchor/generated value observed | BLOCKED: production credential/protection proof and isolated write missing |
| eLMAC | Reptile Sim Load M4:M53 | 25 direct time constants, no formula anchor/generated value observed | BLOCKED: production credential/protection proof and isolated write missing |
| Ramp LC | Rain Outbound M3:M50 | Direct time constants, no formula anchor/generated value observed | BLOCKED: production credential/protection proof and isolated write missing |
| Crew LC | Rain Outbound N3:N50 | Direct time constants, no formula anchor/generated value observed | BLOCKED: production credential/protection proof and isolated write missing |
| Block-Out | Rain Outbound O3:O50 | Direct time constants, no formula anchor/generated value observed | BLOCKED: production credential/protection proof and isolated write missing |
| Include/Exclude | Rain Outbound Q3:Q50 | All 48 cells typed Boolean constants; TRUE = excluded | BLOCKED: production credential/protection proof and isolated write missing |
| No Return | Rain Outbound S3:S50 | All 48 cells typed Boolean constants | BLOCKED: production credential/protection proof and isolated write missing |

Raw named-range reads resolved real coordinates (not inferred row offsets):

* Reptile `simdata_unload` = `'Sim Unload'!A4:AG53`. Column 20 is T.
* Reptile `simdata_load` = `'Sim Load'!A4:Z53`. Column 13 is M.
* MotherBrain `inbound_official` = `Inbound!P4:W100`. Column 6 is U.
* MotherBrain `outbound_official` = `Outbound!P4:W100`.

MotherBrain AA4/AA5 import `simdata_unload` column 20, including existing
overnight date adjustments; U4 consumes AA. Rain Inbound G3 looks up column 6 of
`inbound_official`. All 26 populated inbound flights matched uniquely by normalized
flight, tail and origin; their Reptile T time fractions aligned with MotherBrain U.

Rain Outbound L3 imports `simdata_load` column 13. All 25 populated departures
matched uniquely by flight, tail and destination; Rain L matched Reptile M and
Rain identity/planned time matched MotherBrain. Reptile Sim Load A4 itself imports
`official_outbound` from another workbook; do not assume its future row identity
from MotherBrain or Rain ordering. Runtime revalidation remains mandatory.

These are read-only propagation observations, NOT an isolated write test or a
guarantee about future/concurrent sheet edits. Protected/named-range ownership
metadata is not exposed by the connected Sheets metadata action. Blank-looking
cells remain uncertified as non-spill inputs.

## Exact access blocker

Drive ACL metadata lists `neosektor-sheets@neoapps-497005.iam.gserviceaccount.com`
as writer on all three workbooks. Existing code's writer requests the Sheets
write scope; the reader requests readonly. Neither proves what production
credentials/scopes are currently configured and authorized.

ZEUS has no GOOGLE_MOTHERBRAIN_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_JSON
environment configuration and no repository .env. Existing Render SSH access
fails with Permission denied (publickey). No credentials were printed, copied,
changed or obtained from connector tokens. The connected user's Google identity
is not a substitute for the production service account.

Secure production service-account access or authenticated Render execution is
needed to retrieve effective protection metadata and perform an isolated safe
write/readback test. Do not paste private keys/credential JSON in chat. No
operational flight or Google cell was modified. No adapter was configured and
no field is CERTIFIED/ENABLED. ETD remains Neo-only with no writer.

## Narrow code fixes and tests

Two new regressions were demonstrated against the starting implementation:

1. Null/blank/unknown No Return state could be converted to FALSE (missing key
   raised KeyError). The writer now rejects absent/invalid canonical status and
   uses the shared departure-status set, retaining loading and last-ULD states.
2. Numeric zero readback compared equal to Boolean FALSE, incorrectly confirming
   typed checkbox synchronization. Boolean readback now requires actual bool;
   time serial readback requires an int/float, never a bool or formatted string.

Tests: both regressions reproduced first; all 15 focused override tests pass
after the fix. They cover one-shot failure/no retries, blank/reset no-write,
fresh reset reads, active import shields, exact identity/range checks, formulas,
spills, typed values and permissions. Python compilation and diff-check pass.
No genuine service-account integration writes were possible. No migration,
workflow, calculation or UI changes. Safety fixes and audit are local only;
no deployment was triggered because certification remains blocked.

## Copyable handoff

Continue NeoRain #7 in Z:\DevProj\NeoApps on main only. Fetch/reconcile safely.
Read this audit, the release audit and latest issue #7. Preserve the tested local
typed-readback/No Return safety fixes, #3–#6, existing overrides, artwork and stashes.
Do not restart the implementation.

First resolve secure production service-account access or existing Render SSH
authorization; never paste/export secrets into chat. Then verify configured
account and OAuth write scope, effective sheet protections, exact current
sort/date/flight/direction/tail/route identities and non-spill manual-input
contracts. Named-range propagation coordinates and 26/25 matching observations
are recorded above. Test actual writes only in an isolated safe workbook/input
with that service account, then enable each independently certified destination.

Approved: MotherBrain Inbound AB4:AB50; Reptile Sim Unload T4:T53 and Sim Load M4:M53;
Rain Outbound M/N/O/Q/S3:50. ETD has NO approved writer. Never enlarge STA beyond 50.
Neo saves first; at most one attempt per new edit; no automatic retries or replay.
Blank/unset/reset/deleted overrides produce zero writes. Explicit FALSE is typed.
Never write formulas/spills, guess rows or auto-create Google flights. Reset must
read fresh upstream, leaving later independent Google edits untouched.

Run focused regression/integration tests, verify operational safety before push,
commit main, allow Render autoDeploy only and verify exact SHA LIVE/health.
Report each field honestly as CERTIFIED/ENABLED or BLOCKED with exact evidence.
