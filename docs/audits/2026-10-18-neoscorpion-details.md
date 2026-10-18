# NeoScorpion Dispatch DETAILS cleanup

Baseline: main/origin main `1b69766d1463f4254860c8dfef848dc0919fdd79`.
Scope: presentation, navigation and edit-safe live refresh only. No schema,
fuel calculation, physical milestone, submission, inventory-posting, promotion,
PROF or SPEAR policy changes.

## Control inventory and placement

| Region | Controls / evidence |
| --- | --- |
| Action Required | Missing mission data; current physical review; earlier-visit verification with exact Work/sequence/receipt; unresolved physical source recovery; planned-truck review; handoff dependencies; resume reviewed holds; CALL DISPATCH acknowledgment. Truck serviceability/inventory approvals point directly to the existing truck card, not cloned mutation forms. |
| Current Fueling & Next Truck | Unfinalized visits with physical tail, Fueler, truck, ON/OFF, segment T/F, submission status; exact VISIT DATA; existing planned-truck forms. Current eligible COMPLETE remains on the primary row, without a duplicate DETAILS verification. |
| Fuel & Flight Data | Responsive aligned facts, flight/aircraft/parking/time, fuel values and load-planning COPY. No separate repeated status, submission and load-planning cards. |
| Notes | Compact existing Tail Notes editor; independent current-visit notes labeled by physical tail/truck/cycle/visit. Finalized/interrupted notes stay with their historical visit. No assignment-wide fallback for unknown historical notes. |
| History & Secondary Actions | Finalized visits and frozen cycle summaries; advisory tail acknowledgment; optional swaps, corrections, reopen, end early, cancel/unassign, completed-cycle uplift/defuel; optional source handoff correction; SPEAR explanation/note. One native disclosure per secondary activity, no accordion inside an accordion. |

Tail acknowledgment remains advisory, not a newly invented mandatory action.
No operational form route, input name, validation or identity contract was removed.
Existing native fallback behavior is preserved; existing asynchronous actions,
COPY, notes and live updates remain asynchronous.

## Live behavior

Existing mission DETAILS and completion-specific scroll restoration remain intact.
Inner disclosures also retain their expansion across replacement. Read-only facts,
load-planning output, visit notes/history and independent actions refresh without
replacing focused/dirty controls. Correction/resource/secondary drafts now participate
in dirty protection. Tail Notes keeps its own successful-save baseline rather than
being trapped by the generic protected-input baseline.

Stale identities still defer/disable changed controls; no new verification eligibility
is invented. Mobile stays read-only with its five-column table. Normal Dispatch and
Board View share the same compact console.

## Validation

- Before/after server-render inventory compares every existing form route,
  non-CSRF input/select/textarea name and associated form, plus submit action URLs.
  No nested forms. Separate source verification identities/receipts preserved.
- Focused service tests cover submission, continuation, independent verification,
  status, visit notes/history, permissions, CALL DISPATCH and truck-review links.
- JavaScript tests cover completion scroll (both axes), native redirect restoration,
  inner disclosure persistence, sorting, independent collapse, stale responses,
  dirty-screen verification and Tail Notes.
- Actual Chromium and WebKit desktop Dispatch / mobile Fueler test: plan B, explicit
  ON/OFF, final-data save, A submission activates B, source correction, three concurrent
  required actions, independent resolution, A verified while B ON, B completion,
  one event per transfer, history T/F 3000/2000, COPY equals displayed text.
- A focused Tail Notes draft stays focused and unchanged while OFF arrives in the
  live timeline; saved draft does not prevent later completion refresh.
- Desktop 1920/1440/1024, Board View, mobile 390/375: no page horizontal overflow,
  five mobile columns/read-only controls, retained 98/73px primary mission heights.
- Separate browser status and completion-scroll workflows passed.
- Compilation, JS syntax and diff-check are release gates.

Browser evidence: `instance/browser-evidence/mobile-drawer/continuation-*.png`
(local, not committed). Tests use isolated SQLite/server fixtures; no production
aircraft writes. No PostgreSQL migration/transaction changes are introduced by
this presentation release, so no new migration certification is claimed.

Initial enhanced browser runs exposed an uppercase save-message expectation and
the generic Tail Notes baseline conflict. Both were corrected; final enhanced
Chromium/WebKit run passed. No critical assertion was removed.

Release results: 70 focused service/render tests and 17 JavaScript tests passed.
The enhanced continuation, status and completion-scroll browser workflows passed.
The original-tail browser fixture was updated from the retired prior-work drawer
to the direct Action Center form; it now checks the persisted Work ID, actual
chronological sequence and submission version before verification, retaining
all A→B→A event-identity and 4000-gallon ending-balance assertions.
That Chromium/WebKit A→B→A browser workflow passed, making four passing browser
workflows in total. Expected TRUCK STILL FUELING rejection logs prove the overlap
guard remains enforced; they are not ignored test failures.
