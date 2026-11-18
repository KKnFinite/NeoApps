# NeoSubZero global OUTBOUND correction

Starting source: `1539d759f7e125aab125cb1a315e95f444d36247` (latest `origin/main`, rechecked November 18). Scope is all gateways, sorts and physical tails. No production operational test writes or schema changes.

## Confirmed causes and correction

`mutate_departure_deice()` incorrectly treated CONFIGURED as exclusive ramp occupancy through `_validate_ramp_configuration_available()`. A second parked aircraft returned HTTP 409 even before either aircraft started. The initial synthetic reproduction is retained in `instance/subzero-global-outbound-before.log`.

Removed that readiness-only restriction. INITIAL CONTACT, CONFIGURED, CLEAR CONFIGURED and safe RESET remain aircraft-specific. The canonical operation lock, physical queue projection and `_validate_throat_start()` remain authoritative for actual spraying. Concurrent configuration may succeed for two aircraft; concurrent START on one pad still has one winner. FIN and RADIO PENDING retain occupancy until RADIO COMPLETE.

Desktop OUTBOUND now uses five horizontal columns: Aircraft, Parking/ETD, Status/Actions, Pass 1, Pass 2. The existing shared treatment form provides Type I, Type I + I and Type I + IV, surfaces, manual HH:MM entry and existing clock quick actions. Type I headings are orange and Type IV green. FIN and final-required-pass PILOT START remain canonical service calculations.

Routine treatment saves reuse the existing serialized autosave queue, clock validation, timeout handling and stale-version protection. Invalid/incomplete time drafts are retained. A committed mutation returns its exact event ID/version receipt; only the submitted form can advance to its own acknowledged version. Unrelated dirty forms keep their original versions.

The existing native inline mission disclosure is retained. Desktop routine controls remain visible while aircraft identity expands the additional controls underneath. Ramp Columns and mobile retain their summary/expansion behavior. RADIO opens the canonical inline readback; collapse alone never confirms it. Peer RADIO completion removes the confirmation action and closes the expanded section.

Browser validation exposed two integration issues in the new always-visible row: the lazy details placeholder could retain outdated primary START/END controls, and retaining an original-tail draft could also retain its obsolete visible identity. Reconciliation now refreshes primary controls/identity while preserving dirty forms and their original physical-tail/version evidence. Programmatic live reconciliation suppresses autosave triggers from focus restoration. Errors appear on the affected mission.

## Files

- Services/routes: `app/services/neosubzero_departure_deice.py`, `app/neonodes/neosubzero/routes.py`, `app/neonodes/neosubzero/operations_routes.py`.
- Presentation: `app/static/css/neosubzero_operations.css`, `app/static/js/neosubzero_operations.js`, `app/templates/neonodes/neosubzero/{operations.html,_operations_mission.html,_operations_outbound_controls.html,_departure_deice.html}`.
- Tests: `tests/test_neosubzero_global_outbound.py`, `tests/test_neosubzero_outbound_coordinator.py`, `tests/test_neosubzero_outbound_redesign_postgres.py`, `tests/browser/test_neosubzero_global_outbound.py`, `tests/browser/test_neosubzero_outbound_redesign.py`.

The retired single-CONFIGURED assertion was replaced with canonical independent configuration coverage. Browser selectors and save expectations now exercise the actual horizontal/autosave interface; stale identity, peer confirmation and dirty-draft protections remain asserted.

## Focused evidence

| Check | Evidence |
| --- | --- |
| Global contracts, redesign and Coordinator: 21 passing | `instance/subzero-global-outbound-python.log` |
| Existing Operations/PRETREAT/permission contracts: 21 passing | `instance/subzero-global-outbound-operations.log` |
| PostgreSQL: 3 passing independent-transaction races | `instance/subzero-global-outbound-pg-ready.log` |
| Global Chromium/WebKit plan, layout and RADIO workflow: passing in both engines, 43.473 s | `instance/subzero-global-outbound-browser-verified.log` |
| Existing inline, dirty-draft, tail-change and peer RADIO regressions: 3 tests passing, 98.178 s | `instance/subzero-global-outbound-regression-verified.log` |
| JavaScript | `node --check`, UCC live/draft/conflict harness, four reporting clipboard tests |
| Compilation and whitespace | Eight changed Python files compile; `git diff --check` |

PostgreSQL races verify two CONFIGURED successes, exactly one physical START, and reset versus START atomicity. Browser coverage includes 1024/1366/1920 desktop lists, Ramp Columns, 375/390/430 mobile, all three plans, manual times, inline errors, retained drafts, stale HTTP 409, final-pass PILOT START, pink RADIO PENDING, collapse without confirmation, gray completion and single audited peer confirmation. Screenshots are retained under `instance/browser-evidence/subzero-global-outbound/` and `instance/outbound-inline/browser/`.

Early runs exposed outdated browser save/select assumptions, a detached-element race while capturing a live screenshot, and layout/reconciliation issues corrected above. Final named logs distinguish verified results from those diagnostic runs. Existing datetime/SQLAlchemy deprecation warnings were not suppressed. No broad application suite was run.

## Preservation and release verification

No PRETREAT workflow, DUCC queue engine, historical evidence, gallons, permissions, FROST policy or scoped authorization configuration is changed. Unrelated files, jobs and synthetic PostgreSQL directories remain preserved. The previously stopped local PostgreSQL server was recovered in its existing directory without a reset or deletion.

Read-only authenticated production OUTBOUND check before release reported no current Sort. Render service `srv-d8buq2mgvqtc73e375j0`, authorized My Workspace, remains on commit-triggered main AutoDeploy. Release verification must match the exact pushed SHA, then check bootstrap/worker startup, `/healthz`, `/login`, CSP/HSTS, the versioned Operations assets and authenticated OUTBOUND. The final completion message records the exact LIVE SHA/deployment ID; no synthetic operational action is permitted in production.
