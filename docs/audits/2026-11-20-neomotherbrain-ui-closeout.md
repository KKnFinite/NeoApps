# NeoMotherBrain page-body closeout

Baseline `64f2881c09412b4659c5f31a144950cb78c93061`. The route and template inventory identified 26 in-scope page states. Signed-in browser checks used synthetic records at 1440 × 900 (Chromium), 390 × 844 and 820 × 1180 (WebKit).

## Page inventory

| Page or states | Disposition | Evidence or correction |
|---|---|---|
| Master Schedule board | Accepted | Weekdays and Active autosaved. The generated mission kept its ID through cancellation and reactivation. |
| Manage Sort and selected Sort Overview | Accepted | Sort selection, global and wave windows, and current-Sort identity were legible at all three widths. Updating and clearing a synthetic override preserved the wave windows. |
| Arrival Planning and Departure Planning | Accepted | Current missions, ALP preview and review, arrival spares, and mobile mission rows remained available. A remote tail change updated its own mission while an unsubmitted draft survived. |
| Parking Plan | Accepted | Ramp layout, conflict jumps, drag/drop and mobile HOT/notes worked. Refresh deferred while a note was being edited. A stale optimizer preview could not apply. |
| Parking Rules | Accepted | Flight rules, restrictions, optimizer settings and per-row controls remained accessible. |
| Gateway Matrix and Sort Timeline | Accepted | Weekday/Sort associations, save feedback, time inputs, monthly previews and special-poll controls remained accessible. |
| Manage API and Unmatched Queue | Accepted | Current Sort, result and exception rows, explicit actions, technical details and replay remained distinct. Idle checks outside Sort hours observed no background requests. |
| System Settings and Integrations & Migration | Accepted | Tools, active authority and integration modes remained visible. More technical information could be opened in place. |
| Node Refresh Timings and Google Current Sort Preview | Accepted | Screen-specific values and permitted intervals rendered. Opening details did not reread Google. Explicit read and reread each called the stub once. |
| Arrival Board and Departure Board | Corrected | Each body now identifies the board as well as the gateway, Sort and date. Cross-board and Overview actions follow the destination page’s view permission. |
| Mission Add and Mission Edit | Corrected | Both identify the selected Sort. A view-only user sees disabled fields. The shared NeoRain rendering keeps its existing presentation. |
| Mission Detail | Corrected | Added Sort context, compacted the field list and gated Edit/Delete on the existing Manage Sort edit permission. |
| Master Schedule Add and Edit | Corrected | The previous six-track grid overflowed the 820 px viewport to 1084 px on Add and 996 px on Edit. Fields now reflow within the tablet width. Both modes identify the gateway and respect read-only access. |
| Master Schedule Bulk Edit | Corrected | Matched the editor heading and view-only controls. The wide desktop worksheet still scrolls within its table; the phone uses its existing per-record editor. |
| Master Schedule Detail | Corrected | Added gateway and Sort context, compacted the field list and gated Activate/Deactivate on the existing Master Schedule edit permission. |
| Nightly Operations | Corrected | At phone width, per-Sort links previously lay past the right edge of the table viewport. The labeled record now puts them below the same Sort’s values. |
| Generate Operation | Corrected | Simplified the heading and eligibility explanation. View-only users can inspect the form and return to Nightly Operations. |

MotherBrain Home, the NeoGateway launcher and shared sign-in are landing or shared surfaces. Navigation, node permissions and their owning pages remain tracked separately in #25–27. Optimizer previews and ALP previews are states of the listed pages. Live row partials and POST-only actions do not add independent page bodies.

## Verification

- 8 signed-in browser tests passed, including all six phase workspaces, the Master Schedule reconciliation workflow, and the 11 secondary page states. An operator with view access could review the secondary forms, and authorized editors retained enabled actions.
- 58 service/route tests and 6 parking JavaScript checks passed. They covered independent repeated destinations, plain unassigned-wave labels, effective ETD ordering across midnight, protected manual schedule overrides, scoped imports, stale edits and optimizer previews.
- The configured physical Sort window bounded live refresh even when the separate Ops window extended two hours earlier and later. Flight API preflight stopped outside its eligible window. Google polling did not read or lease before the Sort. The browser runs used isolated fixtures and stubbed providers.
- Two unrelated SubZero draft harness checks in `tests/js/csrf_drafts.test.cjs` failed on both the release base and the working tree. Their extracted JavaScript calls `draftContents`, which their VM fixtures do not supply. The other two checks in that file passed. These failures do not establish a fault in production SubZero.
- Screenshots are under `instance/browser-evidence/motherbrain-closeout-before`, `motherbrain-closeout-after` and `motherbrain-closeout-phases` in the same evidence directory. The before and after directories use identical filenames and viewport sizes.

## Remaining limitations

The wide bulk schedule editor and dense flight tables can still require horizontal movement inside the table. The checks cover three representative viewport sizes. Authenticated editing and save checks used local synthetic accounts and records. This audit did not exercise production mutations or assess the role definitions themselves.
