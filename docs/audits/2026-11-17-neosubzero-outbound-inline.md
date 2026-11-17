# OUTBOUND inline missions and RADIO — 2026-11-17

Starting main: `e0a34ae8fef6baed3b9eb4afae59989263dface0`. Fetched origin/main before editing and before release; unrelated untracked files were preserved.

## Delivered interface

One native mission disclosure now serves desktop Dispatch List, Ramp Columns and mobile OUTBOUND. The summary remains part of the same aircraft card and contains its flight/tail, parking/destination/ETD, status, applicable pass summary, canonical final-pass PILOT START and quick actions. Unplanned pass placeholders are hidden to keep collapsed rows compact. Summary clicks toggle in place without page navigation. Existing desktop ALL RAMPS and mobile ramp preferences are preserved.

The drawer template and separate mobile-only mission template were replaced by `_operations_mission.html`. There is no rendered Details aside, modal or separate aircraft header. The shared expanded body retains reason selection, treatment plan/clocks/surfaces, actual truck/crew participation and gallons, notes, optional observations/inspections, and audited corrections. Routine status actions remain available in the summary while collapsed or expanded. Advanced corrections retain their existing disclosure and authority checks.

Pink RADIO PENDING cards show RADIO in the summary. RADIO expands the same mission and brings its canonical script into view. RADIO COMPLETE exists only at the bottom of that readback section and only when evidence and Coordinator authority permit it. The button itself is the explicit communication confirmation; it does not open a popup. Missing scripts show an evidence-review message and no confirmation control. Collapsing never changes radio state. Successful local or peer confirmation closes the expansion, updates the card to gray and preserves canonical completed-group ordering.

## Safety and reconciliation

- Treatment, CLEAR, radio persistence/auditing, pad/queue safeguards, permissions and server version checks are unchanged. Only the read-only quick-action presentation now returns RADIO instead of direct RADIO COMPLETE.
- Header buttons use native form associations with existing CSRF/operation/event/tail/version fields. They do not accidentally toggle the summary. No new operational endpoint or polling system was added.
- The existing polling/draft reconciler is shared across all screen widths. Draft requests are bounded and restricted to rows in the authorized current context. Opening a mission requests fresh canonical values; collapsing invalidates overlapping responses.
- A header action's focus no longer preserves a stale expanded body through a lifecycle transition. Dirty forms retain their original versions, focus and values. Peer radio completion removes stale confirmation controls and records only one audit; a captured stale request is rejected with HTTP 409.
- Explicit reload blurs old fields and temporarily disables the editor until fresh values arrive. This fixes the verified race where new typing could retain the prior inspection token during a reload.
- After a tail change, the main mission line updates to the authoritative new tail while the old-tail draft remains clearly marked for review. Its physical-tail token stays unchanged and stale saves are rejected. Explicitly confirmed reload replaces that draft with the current tail context.

No schema, stored-event, spray-service, DUCC, Crew/TV, PRETREAT-service, staffing, FROST artifact or production operational-record changes were made.

## Focused verification

| Validation | Final result |
|---|---|
| Operations, OUTBOUND lifecycle/reset/reasons and radio Python modules | 37 passed; 4 passing subtests |
| New Chromium/WebKit full lifecycle and inline List/Columns/mobile checks | 2 browser cases passed |
| Existing mobile status/fluids and mobile Dispatch/PRETREAT/DUCC preservation workflows | 4 browser cases passed |
| Existing optional physical-inspection network/conflict/correction workflow, both engines | 1 browser case passed after fixing reload race |
| Current-tail/old-draft isolation, stale save and explicit reload, both engines | 1 browser case passed |
| JavaScript | UCC live/draft checks and 4 reporting tests passed; Operations syntax passed |
| Compilation / whitespace | Passed |

Eight unique browser test cases passed. Independent Coordinator accounts confirmed RADIO, recording the actual peer user and timestamp with exactly one event audit. The captured original request was rejected after confirmation. Missing-script binding was checked separately in Python; existing service tests protect against fabricated readbacks. Browser checks covered 1366×768 and 1920×1080, mobile 375/390/430px, expansion/collapse, no Details/dialog DOM, no new windows, inline geometry, no horizontal overflow, draft retention, version conflicts, and pink-to-gray sorting.

Logs are retained in ignored `instance/outbound-inline-*.txt`. Counts are unique final cases, not accumulated reruns. Existing datetime/SQLAlchemy warnings were not suppressed. The broader application suite and old inherited full-layout matrix were not re-certified as a whole; this is focused release evidence. PostgreSQL was not rerun because no persistence or locking behavior changed.

## Screenshots

All records are isolated synthetic fixtures, not production operations.

- [Desktop before](artifacts/2026-11-17-outbound-inline/before-desktop.png) / [desktop after](artifacts/2026-11-17-outbound-inline/after-desktop.png).
- [Mobile before](artifacts/2026-11-17-outbound-inline/before-mobile.png) / [mobile after](artifacts/2026-11-17-outbound-inline/after-mobile.png).
- [Inline mobile RADIO](artifacts/2026-11-17-outbound-inline/radio-mobile.png) / [RADIO in Ramp Columns](artifacts/2026-11-17-outbound-inline/radio-ramp-columns.png).

Release uses existing main AutoDeploy. Exact LIVE SHA/deployment and read-only health, bootstrap/worker, published-asset and authorized-page checks are reported with the release. No production test mutations, historical repairs, job cleanup, GitHub Issues or temporary CI workflows were performed.
