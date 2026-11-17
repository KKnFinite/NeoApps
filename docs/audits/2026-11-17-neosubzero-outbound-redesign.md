# OUTBOUND Dispatch operational redesign — 2026-11-17

## Scope and release sequence

Both queued requests were retained. Smart Login shipped first as `b5c98cd528e1c4295bdd28d751ce13bd13071a66`, Render `dep-dbu94th5efls73c7cpcg`, verified LIVE. This report accompanies the subsequent complete OUTBOUND release. The existing desktop checkout is `Z:\DevProj\NeoApps`; the requested `C:\DevProj\NeoApps` path does not exist. Origin/main was fetched and matched the Smart Login commit before the second release.

## Operational changes

- Shared card controls serve desktop Dispatch List, Ramp Columns and expanded mobile cards. They expose only applicable INITIAL CONTACT, CONFIGURED/CLEAR CONFIGURED, RESET, NEGATIVE DEICE, pass-clock, CLEAR and RADIO COMPLETE actions. FIN remains automatic from required pass evidence.
- RESET retires only unstarted planned/configured initiation. It uses existing operation/event locks, physical-tail ownership, Coordinator authority, optimistic versions and audit entries. Actual milestones, pass snapshots, official participation, gallons, FIN and completed communications block ordinary reset. The canonical event selector and tail-state synchronization exclude retired initiation, preventing phantom active work; re-initiation creates a distinct auditable event. Retired configuration cannot block a subsequent valid configuration.
- One nullable `reset_at_utc` column is added through the existing additive bootstrap. Historical rows stay NULL; no records are deleted or backfilled. PostgreSQL upgrade/idempotency and concurrent RESET versus START were tested in isolated schemas.
- Existing CLEAR → pink RADIO PENDING → explicit RADIO COMPLETE behavior is retained. Pending aircraft stay active and protected by the existing pad rules, including when MotherBrain has already recorded departure. Radio confirmation records the existing actor/time/audit and moves the card to the completed group. Missing final-pass evidence exposes review rather than inventing a script.
- PILOT START uses the canonical actual final required pass START. Full blue PRETREATED presentation uses all four canonical pretreatment milestones and yields to a higher-priority departure state. A legacy pretreat flag alone is insufficient.
- Details now has one aircraft header, compact treatment controls, independent reason selection, notes, actual participation/audited corrections and optional inspection. Routine Reload and obsolete duplicate facts/crew editors/history links are removed. Reload becomes available for draft/conflict/network recovery.
- Reason options derive from `CRYOTECH_REASON_DESCRIPTIONS`, excluding the PRETREAT-only code, plus the existing Nervous Captain value. Codes/descriptions normalize to canonical values; arbitrary text is rejected. Existing legacy values remain displayable. Changes to recorded evidence require an audited correction reason and do not rewrite frozen pass snapshots.
- The explicit RECORD PHYSICAL INSPECTION action replaces the two bulky attestations. It records an actual submitted observation with existing actor/tail/location/time/nonce validation; it does not claim independent parking verification. Legacy clients retain their explicit confirmation path. Testing evidence remains excluded from FROST training/validation.

No DUCC TV/Crew source files, queue model, polling cadence, operational crew management or frozen FROST artifacts were changed. PRETREAT treatment behavior is preserved. A pending Details response is invalidated when the drawer closes, preventing it from reopening after navigation. Quiet reason saves keep inputs protected until fresh versions arrive; failed requests retain drafts. Incomplete timestamps are not autosaved.

## Focused validation

| Check | Measured result |
|---|---|
| OUTBOUND reset/reasons/final-pass tests, Operations, radio, spray, physical inspections and PRETREAT | 71 passed; 13 passing subtests; 1 pre-existing endpoint-name assertion failed |
| Coordinator-only reset/undo/radio authorization after extending its assertions | 1 passed (included in the 71 above) |
| Disposable PostgreSQL RESET/START race and additive bootstrap/history preservation | 2 passed, no skips |
| New Chromium/WebKit full lifecycle and layout workflows | 2 passed |
| Existing mobile Dispatch/PRETREAT/DUCC workflows and color checks | 4 passed across original run and targeted WebKit retry |
| Existing DUCC TV weather workflows, unmodified tests | 2 passed |
| Existing inspection correction/draft/conflict browser workflow, both engines | 1 passed |
| JavaScript | UCC live/draft/conflict checks and 4 reporting tests passed; Operations syntax passed |
| Compilation and diff whitespace | Passed |

The one existing Python failure is `NeoSubZeroPretreatTest.test_routes_and_permission_defaults`: it requires the retired `neosubzero.coordinator` endpoint name. HEAD already uses `legacy_coordinator_redirect`; its bookmark redirect and retired revision behavior pass the current Operations tests. This unrelated assertion was left intact. No failures were skipped or marked expected; existing datetime/SQLAlchemy deprecation warnings remain visible in logs.

The initial existing WebKit workflow completed its functional assertions but failed when an element screenshot detached during normal live refresh. Capturing the page instead avoids that screenshot-only race; the targeted workflow then passed. No live-update functionality was disabled.

New browser coverage exercised 1366×768 and 1920×1080 desktop views and 375/390/430px mobile views, both engines: quick reset/re-initiation, one/two-pass clocks, no incomplete autosave, draft protection, peer-change HTTP 409 recovery, explicit radio confirmation, sorting and no horizontal overflow. Existing DUCC TV/Crew, inspection and PRETREAT browser checks were retained.

## Before / after evidence

Synthetic aircraft only. Comparable Alpha aircraft details are captured in:

- [Desktop before](artifacts/2026-11-17-outbound/before-desktop.png) / [desktop after](artifacts/2026-11-17-outbound/after-desktop.png).
- [Mobile before](artifacts/2026-11-17-outbound/before-mobile.png) / [mobile after](artifacts/2026-11-17-outbound/after-mobile.png).

Additional Chromium/WebKit viewport and radio-pending screenshots and full warning/test logs are retained under the ignored `instance/outbound-redesign/` and `instance/outbound-*.txt` paths. No broad application audit was run. This is focused release evidence, not a full-system green claim.

## Production preservation and verification

Production verification is read-only: exact Render AutoDeploy SHA/status, bootstrap and worker startup, health/login responses, security headers/CSRF, published Operations CSS/JS and authorized page reads. Final deployment ID and SHA are reported with the release; this document deliberately does not invent a self-referential commit ID. All synthetic mutations occurred locally. Existing unrelated files, jobs, histories and synthetic PostgreSQL files were preserved.
