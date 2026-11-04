# Pre-Seniority and isolated Shift Staffing TEST mirror

Starting fetched main: `91ec0a25fe4e87c0381d27737e6c0010ddb2c15e`.

## Classification

`pre_seniority` / Pre-Seniority is database-valid, writable and non-management. Shared editing, employee filters, roster projections and bulk choices use the canonical classification lists. Bulk input also accepts the display spelling. It does not confer management authority or invent a seniority date; existing non-Seasonal date rules and union-vacation classifications remain unchanged.

Explicit PostgreSQL bootstrap transactionally widens the existing classification CHECK under the established advisory lock and five-second lock timeout, without rewriting employee rows. Existing SQLite installations use the established copy/constraint upgrade with foreign-key/index/trigger preservation. No employee classification is automatically changed.

## Source and destination

The only writable workbook is `1WakfSwELioqBXbkd4LYd8CJ8BDHRLdsVfuzd0JjMgns`, tab `Shift Staffing`, numeric sheet ID 0. The workbook ID is a constant, validated again against Google metadata. It cannot be supplied by a browser, integration setting or environment override. The production workbook `1j_A1oeGCv92EiylwyOcj_1By_mc1Nakmb_rVdw6QrCY` was not accessed or modified.

The existing canonical Night → Ramp → Shift roster and Shift Flow shorthand drive the mirror. Names are LAST, FIRST, ordered deterministically. Door employees group by explicit Final Door. Discharge employees remain under DIS, with their explicit destination Door in the existing DIS assignment column. Missing Final Door employees appear in BD12:BD203 and leave that column after assignment. Duplicate placements, unknown Doors, unresolved codes, unexpected formulas in employee-owned cells and capacity overflows fail before any batch is sent.

Only name/assignment-code cells and the repeated permanent **S** classification columns are authoritative writes. Permanent values are PT, FT (all Combo variants), SEA and PRE. The existing **C** classification columns are distinct from assignment-code column C: C values/formulas are preserved. The FT dropdown options, existing DFT summary criterion/label and DFT conditional-format criterion recognize FT. The dormant legacy NFT summary/rule remains intact rather than deleting existing formulas or unrelated formatting. All writes are field-masked; no rows, columns, supervisors, formulas below the input blocks, formatting or other tabs are rebuilt.

## Durable automatic delivery

One additive singleton table, `staffing_sheet_sync`, contains revision/lease/retry/checksum metadata, without employee payloads. ORM employee, assignment, Shift Flow and hierarchy changes atomically dirty its revision in the canonical transaction. Rollback removes the intent too. Employee requests perform no Google I/O.

The existing deadline scheduler drains this receipt; no new permanent thread, timer, worker service or polling interval is added. Local commit hints coalesce through its existing 30-second busy cadence. Restart/missed hints are recovered from the durable row on the existing aligned ten-minute safety scan. A five-minute claim lease and token fencing coordinate processes. A late superseded attempt requests reconciliation again so it cannot strand an older snapshot behind a newer verified receipt.

The PostgreSQL roster snapshot uses repeatable-read. Database sessions are released before external I/O. Existing Google credentials, write scopes and bounded diagnostic transport are reused, with five-second configured request timeouts, bounded authentication, a 4 MiB wire/decompressed response ceiling, and operation-owned client/session cleanup. No retrying SDK client is introduced.

Each attempt first reads the target, updates only changed authoritative blocks/rules in one atomic batch, then independently reads back employee values and classification rules. Only matching content receives `verified_at` and a checksum. An accepted write with a lost response is re-read before any replacement; an identical Sheet requires no further write. Failures remain pending with 30-second exponential backoff capped at 600 seconds. New staffing edits do not bypass an existing provider-failure cooldown. Shift Flow shows VERIFIED/PENDING and a concise error, including capacity overflow.

## Actual TEST workbook result

An authorized initial write used fresh read-only NeoStaffing source data and the exact allowlisted test workbook. 164 employees were synchronized: 132 in Door groups, 19 in DIS and 13 UNASSIGNED. The largest Door group was 14 of 20 rows; DIS used 19 of 59; UNASSIGNED used 13 of 192. No overflows occurred.

Read-back verified every expected employee cell and produced zero remaining writes. All cell formatting, C values/formulas, supervisor cells, values/formulas outside the precise patch and unrelated-tab metadata matched the pre-write snapshot. Only sheet ID 0 was in the write batch. Private source/target evidence remains in ignored `instance/staffing-test-sync-20261104/`, outside Git. The existing service account already had writer access; no sharing/credential/hosting changes were made.

## Focused validation and limitations

- 54 focused Python cases passed, including the current classification/editor/schema and existing deadline scheduler suites; 19 passing subtests.
- 11 unique PostgreSQL cases passed across the final ten-case run and the additional late-attempt case: bootstrap preservation, concurrent dirty revisions, one worker claim, rollback, retry, and no database transaction held during Google I/O.
- Three existing seniority JavaScript cases passed.
- One synthetic browser method passed in Chromium desktop and WebKit mobile, covering actual Pre-Seniority editing, date requirements and the filtered roster.
- Compilation and `git diff --check` passed. Existing datetime deprecation warnings remain visible.

The Google browser was not authenticated, so native Google rendering could not be inspected. Connector read-back verified formatting and structure instead; synthetic NeoStaffing screenshots are retained. No broader regression inventory was run. No production employees, historical assignments, timecards or operational records were changed by tests. Existing unrelated files, artwork and synthetic databases were preserved.

Release uses main and Render AutoDeploy. The delivery message records the exact LIVE SHA/deployment ID and read-only runtime receipt/health verification. Future capacity/layout/permission errors keep the mirror pending and visible; they do not undo canonical staffing saves.
