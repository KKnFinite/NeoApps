# NeoSubZero: explicit radio completion and departure ordering

Starting main and previously LIVE commit: `f6fbd65d9ca0995456c034fb08b395f6b45c93a5`.

## Verified behavior

Sprayed departures now follow FIN → CLEAR → CLEARED · RADIO PENDING → explicit RADIO COMPLETE. CLEAR preserves its original timestamp and treatment status. A separate canonical departure-event radio state records the confirming Coordinator and actual UTC confirmation time. Opening, displaying or copying a script does not confirm it. Confirmation requires existing Coordinator edit permission, operation/mission/tail ownership and the current event version. Repeating a confirmation with the current version is an unchanged success; a stale version conflicts. Neither path duplicates an audit.

RADIO PENDING is hot pink (`#FF1493`) in Operations list/visual layouts and the shared desktop/mobile DUCC projection. It retains Position 10 and blocks queue corrections around the active aircraft and subsequent throat START. Explicit confirmation makes the Operations card gray, releases the throat and advances Middle/Position 9. The readback retains the final actual pass START and frozen fluid/concentration evidence, including Type IV at 100%. Missing gallons do not block any of these actions.

OUTBOUND sorts active aircraft by ETD, then pending-radio aircraft by ETD, then completed aircraft by descending completion/Block-Out time with deterministic identity tie breakers. Visual columns retain that order within each ramp. Actual Block-Out without a departure event, required/requested deice or recorded departure spray immediately displays gray GONE · NO DEICE PLANNED. It is only a projection: no NOT SPRAYED, spray or FROST outcome is created. A later explicit deice request returns the aircraft to active treatment. Pretreatment history is retained. Explicit NEGATIVE DEICE remains red; explicit NOT SPRAYED retains its separate meaning/style.

Desktop all-ramps and mobile ramp preferences remain unchanged. No independent mobile movement logic was introduced.

## Physical-tail safety

A verified edge-case reproduction showed that detaching a pending physical tail from its source mission could release the previous projection. Pending events now remain on the shared physical board with a canonical identity-review warning. A recorded DEICE PAD/ramp snapshot retains physical occupancy even if the source mission is reset or its parking row changes. No replacement Block-Out timestamp is invented. An unlocated pending aircraft requires canonical location review before another throat START. Radio confirmation cannot use a mismatched mission/tail; an ordinary flight swap cannot move spray history to the replacement aircraft. A valid mission swap that still references the original physical tail can resolve that tail's pending event, retaining original mission provenance.

## Backward-compatible schema

The existing advisory-locked, bounded schema bootstrap adds three nullable columns to `neosubzero_departure_deice_events`: `radio_state`, `radio_completed_at_utc`, and `radio_completed_by_user_id` (users foreign key). No status enumeration change, data backfill or historical repair is performed. NULL means legacy/no-radio evidence, never a claimed confirmation. Pre-release CLEARED records remain completed and do not obstruct the new throat workflow. PostgreSQL tests remove the optional columns, apply bootstrap twice and verify original records/timestamps are unchanged.

## Validation

All mutations used isolated SQLite/PostgreSQL fixtures with providers mocked or disabled. No production operational tests, Google writes, R2 writes, historical repairs, load tests or hosting changes were performed.

- Focused Python: 149 passed, plus 4 passing subtests. Covers canonical spray/FIN/CLEAR/radio actions, requests, immediate no-deice ordering, negative/legacy states, late gallons, historical corrections, frozen readbacks, tail ownership and archive verification/reconciliation.
- PostgreSQL: 9 passed. Independent transactions prove one winner for competing CLEAR/radio writes, correct START serialization, retained tail identity, queue concurrency and additive bootstrap preservation. The local database was stopped afterward; its synthetic directory was preserved.
- JavaScript: 5 passed, including shared UCC refresh/draft/conflict behavior and reporting; three affected scripts also pass Node syntax checks.
- Browser: 7 passed (Chromium/WebKit workflows), including the complete new radio lifecycle, existing Operations/PRETREAT layouts and shared physical-ramp/participation suites. The two radio cases are additionally rerun after the final tail-review projection change.
- Python compilation: 18 changed/new Python files passed.
- `git diff --check`: passed.

Final Render release identity is recorded in the release completion report after AutoDeploy. Browser fixtures exercise Chromium and WebKit, Operations list/visual layouts at 320/375/390/430/1366/1920 pixels, shared desktop/mobile DUCC, explicit confirmation/queue release, blocked START with draft preservation, staffing drafts, Driver gallons and audited post-CLEAR corrections. Local machine-readable results are in `instance/subzero-radio/`; screenshots include `browser/*operations-*-pending.png`, `browser/*ducc-*-pending.png`, and `browser/*ducc-*-advanced.png`. Existing viewport suites also cover PRETREAT and larger desktop widths.

Existing datetime/ORM deprecation warnings remain visible; this release does not claim a full-system regression run. Permission changes, fueling logic, MotherBrain authority, FROST forecasting/models, weather collection and archived application formats were not changed. Radio audit records use the existing immutable correction/archive mechanisms.

## Release checks

Only this task's source, tests and report are staged. Unrelated images, NeoRain prototype files, documentation and preserved synthetic PostgreSQL files remain untouched. Release uses a normal main push and Render AutoDeploy. Production verification is read-only: exact deploy SHA/status, bootstrap/startup logs, health/login, authenticated Operations/Coordinator/DUCC, CSP/HSTS and committed asset hashes. Production confirmations and aircraft movements are intentionally not executed for testing.
