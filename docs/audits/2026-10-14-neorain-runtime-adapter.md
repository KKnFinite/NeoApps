# NeoRain #7 — existing runtime connection, diagnostic and write safety

## Continuation: write-fence investigation (2026-10-14)

The owner confirms all eight production diagnostic results were collected and
existing credentials/workbook write permissions are verified. Do NOT repeat the
credential audit or treat the historical pending-access notes below as current.
The initial diagnostic/safety release is LIVE at 76bffea06f1336418b632fd51b90fa3f77e70a0e,
Render dep-db81uuvlk1mc7399lvug, with HTTP 200 health.

Google's Sheets MCP Developer Preview now documents `writeControl.requiredRevisionId`:
https://developers.google.com/workspace/sheets/api/reference/mcp/tools_list/update_spreadsheet
The public endpoint's actual tools/list schema confirms that request field.
This is distinct from gspread's ordinary Sheets REST batchUpdate and is a
potential server-enforced comparison, not another read-before-write workaround.
However, get_spreadsheet's documented output is generic JSON and documents no
read-snapshot revision field. A Drive version/ETag must not be guessed as one.
Google also requires Developer Preview access and Sheets MCP API enablement.

A temporary, non-blocking deployment-bootstrap probe makes one fixed, bounded
read-only MCP get_spreadsheet call (MotherBrain Inbound AB4, no grid contents).
It reports only availability/status, never returned revision, credentials,
flight data, or provider exception bodies. It has bounded OAuth and API timeouts,
no write request, no fallback writer, no automatic activation, no app startup
hook and cannot fail schema bootstrap. Remove the temporary bootstrap call after
collecting the capability evidence. This is not another credential audit.

Even an available revision remains CANDIDATE_ONLY until isolated Google tests
prove stale-revision rejection on reorder, formula insertion and concurrent
changes. Formula-driven cross-workbook identity changes must be covered too;
do not assume upstream recalculation necessarily changes a local write revision.
Blank targets still need an independently verified manual/non-spill contract.
All eight operational writers remain disabled; ETD remains Neo-only.

Local validation: 30 write-safety/authority/probe tests and 13 probe/startup tests
passed (4 probe tests overlap). No operational Google cell was modified.

### Actual conditional-endpoint result and release decision

Production execution at 2026-10-15T01:11:24.800Z returned HTTP 403 from
`https://sheetsmcp.googleapis.com/mcp/v1` for the fixed read-only tool call.
`revision_snapshot_available=false`, `operational_writes=0`. Evidence: Render
deployment dep-db82ftrrjlhs73cndig0 at 66e17143f3bd30381230178aa7c3d65bb41c45b5,
which built, bootstrapped and reached LIVE. This is an unavailable separate
revision-conditional endpoint, NOT a failure of the already confirmed ordinary
Sheets credentials/workbook access. No repeat credential audit was performed.

The sanitized response does not establish whether the cause is disabled Sheets
MCP API, Developer Preview eligibility, service-account support or another API
access restriction. Do not invent that reason from a bare 403. Project/API owner
must establish access to this conditional endpoint; no new key or SSH is needed.
Its get_spreadsheet response must then prove a usable snapshot revision, followed
by isolated stale-write/reorder/formula-insertion tests. No fallback to ordinary
unconditional Sheets values.update is permitted.

The temporary bootstrap invocation was removed immediately after this evidence;
normal deployment bootstrap has no Google probe dependency. The bounded helper
and its synthetic tests remain available for an explicitly requested later check.

All eight operational writers remain BLOCKED: no verified server-side write
fence. Blank-looking inputs additionally remain unproven until formula/spill
ownership is established at the same protected snapshot. No isolated Google
write/readback or concurrent-mutation success is claimed. Existing Neo-first,
one-shot/no-retry, zero blank/reset writes and ETD independence are unchanged.

If this conditional API cannot be made available, a different Google-side
keyed/protected input design may be required. Existing approval of eight input
ranges does NOT authorize changing operational formulas, ranges or permissions;
submit the concrete design for separate owner approval before such changes.

Starting local HEAD b657f20a4f8b6b55d7cfc0becff5cab507bc5521 is preserved.
Starting origin/production 2c05915a78754fa51de4742597227471a50ed61b.
Latest issue #7 credential correction and complete prior body/comment reviewed.

## Authentication reuse

No new credential system, secret copy, ZEUS credential requirement or SSH
prerequisite. RuntimeGoogleAdapter uses the existing `_credential_json` and
`_parse_service_account_json` dedicated-or-fallback loader and
`_create_gspread_writer` from google_motherbrain_sheets. It requests existing
Sheets write scopes. A separate read-only Drive metadata scope, using the same
credential source and existing gspread creation helper, asks Google for the
authenticated principal's effective `capabilities.canEdit` per allowlisted book.
NeoSektor's loader/writer and credential environment are unchanged.

## Read-only production diagnostic

`/neorain/google-certification`, also linked from Settings for Grandmaster.
Login, active gateway Rain access AND global Grandmaster authority are required.
GET only. `?field=sta` (or one of the other seven approved fields) performs a
bounded manual check. Other parameters, arbitrary books/cells and ETD are rejected.
No automatic checks on page load. Requests are cached for 30 seconds per field/
gateway to avoid repeated accidental Google calls. HTTP response is private,
no-store. Provider exceptions are sanitized, never returned or logged verbatim.

It reports effective account/source, whether it matches NeoSektor's fallback
account, requested scope, authenticated workbook-read outcome, effective canEdit,
protection counts and manual/formula/generated/unproven-blank counts. It returns
no keys, tokens, raw credentials, employee/flight rows or protection editor lists.
It cannot write data, create Google flights, change permissions or enable writers.
Protected ranges are read directly through the runtime service account rather
than relying on the connector's reduced metadata response. Named-range protections,
sheet protections and unprotected holes are resolved conservatively.

Production results are NOT yet observed: the Chrome runtime tab is signed out.
Sign in with the existing account and run this secured diagnostic after safe
deployment. No demand for new credentials or SSH is appropriate.

## Bounded adapter and the actual remaining write blocker

The runtime adapter implements allowlisted workbook/cell access, exact bounded
identity reads, canonical flight-number normalization, distinct row ordering,
MotherBrain date and full mission manifest/tail/route/planned-time cross-checks,
effective permissions, named/sheet protection inspection and typed readback.
Blank CellData is NOT promoted to a proven non-spill input. Metadata errors,
denied/unresolved protection, merges/generated cells and date/identity conflicts
fail closed. ETD remains Neo-only.

**No operational field is certified/enabled.** None may be enabled by a Boolean
configuration toggle. Ordinary Google Sheets updates cannot condition an A1 write
on the previously inspected flight and formula still being unchanged. Concurrent
collaborators can reorder/change a flight or insert a formula between GET and
update. A transaction batching changes is not a comparison against an earlier
read. No verified Google-side flight/row/formula fence exists in the current
workbook contracts. A reread is not claimed as an atomic guarantee.

This is distinct from authentication and affects all eight approved fields:

| Field | Exact approved input | Status |
|---|---|---|
| STA | MotherBrain Inbound AB4:AB50 | BLOCKED: write fence; blank/spill contract; isolated write proof |
| Block-In | Reptile Sim Unload T4:T53 | BLOCKED: write fence and isolated write proof |
| eLMAC | Reptile Sim Load M4:M53 | BLOCKED: write fence and isolated write proof |
| Ramp LC | Rain Outbound M3:M50 | BLOCKED: write fence and isolated write proof |
| Crew LC | Rain Outbound N3:N50 | BLOCKED: write fence and isolated write proof |
| Block-Out | Rain Outbound O3:O50 | BLOCKED: write fence and isolated write proof |
| Include/Exclude | Rain Outbound Q3:Q50 | BLOCKED: write fence and isolated write proof |
| No Return | Rain Outbound S3:S50 | BLOCKED: write fence and isolated write proof |

Runtime permission/protection observations remain to be collected, independently
of the structural write fence. Earlier 26/26 Block-In and 25/25 eLMAC propagation
evidence is retained; no operational Google values/formulas were touched again.
No destructive integration test or unauthorized isolated target was used.

Google reference: spreadsheets.batchUpdate request body and collaborative-update
semantics: https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate
ProtectedRange requestingUserCanEdit/unprotectedRanges:
https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/sheets#ProtectedRange

## One-shot result integrity

Neo still commits first. Once a write is attempted, transport timeout, missing
readback or wrong typed readback now records UNCERTAIN, not NOT_WRITTEN. No retry
or replay occurs. Explicitly blocked preflight remains NOT_WRITTEN. Failed
preflight I/O remains FAILED. No database/schema changes. Prior typed readback
and absent No Return safety commit remains included.

## Validation and release gate

33 focused Python tests passed: shared credential fallback/dedicated identities,
scope requests, real gspread HTTPClient with GET-only mocked transport/timeouts,
unchanged NeoSektor input writer, all approved bounds, named/sheet protections,
unproven blanks/formulas/generated cells, reordered/normalized/missing/duplicate/
stale identities, protected GET-only diagnostic, exception sanitization/cache,
one-shot UNCERTAIN/no retry, existing overrides and mission controls.
Chromium diagnostic integration passed at 1440×900, 1920×1080, 390×844 and 375×568,
with no POSTs, JS errors or page horizontal overflow. Compileall, inline JS syntax
and diff-check passed. These are isolated fixtures, NOT production Google writes.

Deployment requires fresh operational safety confirmation, requested but not yet
received. Synthetic isolated workbook/range authorization was requested, not yet
received. Do not infer either from absent replies. Production diagnostic execution
requires ordinary existing NeoApps sign-in, also requested. No secrets requested.

## Copyable continuation prompt

Continue #7 in Z:\DevProj\NeoApps, main only. Fetch/reconcile; preserve local
runtime-adapter/diagnostic implementation, b657f20a safety commit, completed #3–#6,
overrides, artwork and protected stashes. Read this audit and latest issue #7.

Use existing production Google credentials; do not request new ZEUS secrets or
SSH. After confirming deployment is operationally safe, deploy the tested
read-only diagnostic and verify exact SHA LIVE/health. Use the signed-in
Grandmaster Chrome session at /neorain/google-certification to collect effective
account/scopes/canEdit/protection evidence. Do not bypass authentication.

All operational writers remain disabled because no Google-side flight/row/formula
write fence is verified. Do not replace that with read-before-write and call it
atomic. Establish a safe contract, obtaining owner approval for any new Google
architecture/formula/permission changes before implementing them. The ordinary
Sheets API batch is atomic only within its own requests, not relative to earlier
GET observations. Keep each uncertifiable field disabled and report its reason.

Obtain explicit isolated test workbook/range authorization; use synthetic data
only and the existing runtime service account for actual write/readback proof.
Approved operational ranges remain MB Inbound AB4:AB50; Reptile Sim Unload T4:T53
and Sim Load M4:M53; Rain Outbound M/N/O/Q/S3:50. No ETD writer. Preserve Neo-first,
typed FALSE, no blank/reset writes, no automatic retries/reassertions/Google flight
creation. Keep UNCERTAIN for ambiguous post-send outcomes. Run focused tests,
push only safe validated changes and verify exact-SHA Render LIVE. Report all
field statuses and actual evidence without equating mocks with Google success.
