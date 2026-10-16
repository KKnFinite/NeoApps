# Dispatch continuation and console change — evidence ledger

Starting main: `9d095f4705a595bc48d877644fa976f4643934c4`.
Fetched before inspection. Prior lifecycle audit read; unrelated NeoRain prototype
and artwork preserved. No production fueling transaction is a test fixture.

## Existing path map (before code changes)

| Concern | Existing owner | Separation required |
| --- | --- | --- |
| Fuel Load edit | `autosave_dispatch_field`, legacy `_save_dispatch_assignment`, `_record_assigned_fueler_update` | One saved Actual must freeze the visit target and plan another visit, not rewrite source evidence. No-Actual edits retain the same work. |
| Pending uplift | `NeoScorpionPendingUplift`, `save_pending_uplift`, `_promote` | Existing records describe post-Dispatcher-completion uplift. Preserve their meaning; identify new continuation plans explicitly. |
| Post-completion START UPLIFT | `start_follow_up_fuel_cycle` | Keep new cycle, completed event and rollback contract; do not use for a truck continuation before verification. |
| Fueler handoff | `submit_fueler_assignment`, submission fingerprint/version | New continuation activation belongs after a committed valid receipt in the same transaction; no automatic Dispatcher approval or fuel posting. |
| Physical identity/history | append-only Work, `_segment_views`, `_locked_source_segment`, `new_work` | Reuse permanent Work IDs and same-cycle visits. Never recycle ON/OFF/Actual/T/F. |
| Dispatcher verification | `complete_fueled_assignment`, independent completion form | Source Work can already finalize without completing replacement. Extend overall completion to await every mandatory visit/plan. |
| Truck availability | `truck_start_gate`, segment records, serviceability and inventory epochs | Busy truck may be planned, never physically ON until free/serviceable. Unposted first transfer remains unresolved inventory. |
| TOP OFF | `complete_nightly_truck_top_off`, append-only TopOffEvent; `attach_top_off_status` in replanning | Forecast display currently decorates every historical open trip. Actual returned/ON evidence must outrank obsolete forecasts without fabricating RETURN. |
| DETAILS | `_fuel_dispatch_panel`, `_pending_uplift`, `_outstanding_work` | Required source verification and review controls are nested; move into one top Action Center and retain optional history/actions. |
| Preferences | user/gateway `NeoScorpionDispatchPreference`, optimistic version | Existing one truck-collapse field shared by normal/board; add independent board and statistics settings, retain old defaults. |
| Live UI | revision/tail fingerprints; critical identity-scoped patch; grouped row sort/scroll | Preserve existing receipt and stale-response protections; update Action Center without replacing dirty inputs. |

## Release gates

New truck B must activate at Fueler A submission, not Dispatcher verification.
Source receipt/baseline must remain independently verifiable and corrections must
raise explicit dependency review rather than rewrite a started descendant.
Legacy pending uplifts must remain distinguishable. No release with duplicate
posting, lost verification, cross-tail carryover or partially migrated schema.

## Implemented contract and confirmed causes

* `target_changed()` is shared by both Required Fuel editors. No Actual means
  the same visit is updated and the existing acknowledged Fueler notification
  shows old/new requirements. One saved Actual, including zero, freezes that
  visit's target and reconciles one outstanding `increase` plan. Decreases never
  create transfers. Live ETD remains live even when the physical target is frozen.
* Existing `_promote()` required a finalized event/Dispatcher COMPLETE. It remains
  the legacy `uplift` path. New `continuation`/`increase` plans instead activate
  from a validated Fueler receipt, under the existing operation/assignment locks.
  Both submitted mobile URLs and service calls use the same activation path.
* Each handoff allocates a new canonical Work ID in the SAME cycle, carries exact
  parent Actual to Remaining, and clears Actual/APU/T/F/ON/OFF/submission. No event
  or inventory deduction is created by planning or promotion. Busy trucks can be
  planned/activated, but their real start gate still blocks ON until released.
* The handoff snapshot records the exact source ID, receipt version/fingerprint,
  final tanks and net NEO. Source verification uses its own permanent ID/sequence
  and current receipt version while later visits are active. Overall completion
  waits for every required visit and outstanding plan, in either verification order.
* Source correction revokes dependent receipts and raises explicit handoff review;
  child readings are never rewritten. Explicit review records actor/reason/accepted
  measured Remaining. A finalized descendant cannot be silently restated.
* Reduced targets retain unneeded work for review. Dispatcher may cancel a planned
  or activated-but-unstarted next visit explicitly. The existing preparation-retirement
  archive preserves its readings and cancellation audit without inventing ON/OFF.
  A started child cannot be cancelled through this path; OFF remains recordable.
* `_close_current_truck_segment()` previously searched only the current inventory
  epoch for outstanding OFF work. Reproduction: same truck visits A then B;
  Dispatcher baseline 5,500; verify B's 2,000 first. It marked 3,500 verified even
  though A's 3,000 was still unposted. The check now uses canonical explicit reading
  coverage across epochs: 3,500 remains UNVERIFIED; posting A yields 500 once.
  A physical reading that genuinely covers prior work still prevents rededuction.
* `_release_resources()` treated any retained historical TOP OFF SENT as a current
  absence. A verified later RETURN now supersedes that prediction in memory only.
  Physical ON without recorded RETURN shows truthful missing-verification review,
  not TOP OFF OVERDUE or invented fuel. Future recommendations remain conservative.
* Required actions had been nested in separate menus; mobile also rendered planning
  forms. DETAILS now starts with keyed Action Required, then current/next visits,
  fuel/aircraft data, notes and secondary/history material. Source verification,
  source/legacy review and handoff/plan resolution are independently addressed.
  Existing row COMPLETE, FOB, optional lifecycle and resource controls remain.
* Required Fuel autosave adopted its own revision while dirty controls prevented a
  full refresh. Browser reproduction showed Saved but no third review action.
  Autosave and lifecycle mutations now request the fast operational Action Center
  patch. Dirty action inputs are protected separately; successful actions mark only
  actually submitted values clean, so a resolved item disappears without losing
  other drafts. Source resubmission does not duplicate the active child card.

## Persistence and compatibility

Six additive columns, supplied by both existing bootstrap/schema-sync paths:

| Existing table | Added columns |
| --- | --- |
| `neoscorpion_pending_uplifts` | `plan_kind` (default `uplift`), `handoff_snapshot` |
| `neoscorpion_fuel_work_states` | `target_frozen` (default false), `continuation_review_reason` |
| `neoscorpion_dispatch_preferences` | `board_truck_cards_collapsed`, `statistics_collapsed` (both false) |

No new truck/mission/physical-work registry, no replacement of historical Work IDs,
no new transfer-posting system, no new PROF formula. Existing plan uniqueness and
Work/event foreign keys remain. Old plans retain post-completion uplift semantics.
Migration does not create an event, deduct inventory, rewrite tanks or ON/OFF.

Normal truck collapse retains its old preference; Board uses its own new field.
Statistics has a separate server user/gateway preference. Sparse versioned patches
do not overwrite other preferences or cause operational writes during polling.
Mobile Dispatch is read-only, with five columns and a separate read-only DETAILS
summary. Desktop controls remain permission protected; screen width is not treated
as an authorization boundary. Existing 98px/73px primary rows and sorting remain.

## Scenario evidence map

S = isolated service/render tests, P = disposable PostgreSQL, B = actual browser.
This table maps coverage, not a claim that every permutation was browser-driven.

| Prompt case | Result / evidence |
| --- | --- |
| 1 No-Actual increase | PASS S/P: same Work, preserved ON/APU, old/new notification |
| 2 One Actual increase | PASS S/P: including a zero Actual, one independent plan |
| 3 Increase while ON | PASS S/P: source ON/Remaining/Actual unchanged |
| 4 Increase after OFF | PASS S/P: source OFF retained, no transfer invented |
| 5 Increase after submission | PASS S/P: frozen source receipt remains independently verifiable |
| 6 Decrease after Actual | PASS S/P: no new transfer; explicit review when plan unnecessary |
| 7 Plan B while A ON | PASS S/P/B |
| 8 Busy B | PASS S/P: plan allowed, ON blocked until real OFF; waiting status |
| 9 A OFF not submitted | PASS S/P/B: B stays planned |
| 10 A submits | PASS S/P/B: fresh B activates immediately |
| 11 A unverified / B proceeds | PASS S/P/B |
| 12 Verify A while B ON | PASS S/P/B: exact source ID/receipt |
| 13 B Fueler completes | PASS S/P/B: fresh Actual/APU/T/F/ON/OFF/receipt |
| 14 Verify all visits | PASS S/P/B: mission completes only when mandatory work resolved |
| 15 Unneeded B | PASS S/P: retained review, no automatic cancellation |
| 16 Explicit cancel | PASS S/P: no fake ON/OFF or inventory; started visit protected |
| 17 Repeated target edits | PASS S/P: one outstanding plan |
| 18 A→B→A | PASS S/P: independent Work/events/tanks/T/F; original A unchanged |
| 19 Concurrent Dispatcher edits | PASS P: optimistic version and operation serialization |
| 20 Tail swap with planned B | PASS S/P: no cross-tail carryover; old plan cancellation explicit |
| 21 Defuel plus other work | PASS S/P: independent real defuel; continuation mixing rejected |
| 22 Planned asset unavailable | PASS S/P: valid source receipt retained; actionable review |
| 23 Source correction after B begins | PASS S/P/B: no silent baseline rewrite; explicit review |
| 24 Three Action Center items | PASS S/P/B: verification, dependency and plan review |
| 25 Resolve one item | PASS S/P/B: other two remain |
| 26 Another row dirty | PASS B: action updates without input loss |
| 27 Dialog open | PASS B: critical dependency update arrives independently |
| 28 Mobile read-only | PASS B: no visible submit controls |
| 29 Five mobile columns | PASS B: five visible cells, no horizontal overflow |
| 30 Numeric alignment | PASS B: matching input top/width and 98px row |
| 31 Statistics preference | PASS S/B: collapse and reload persistence |
| 32 Normal collapsed / Board expanded | PASS S/JS/B |
| 33 Opposite collapse values | PASS S/JS: independent sparse values |
| 34 Returned TOP OFF / fueling | PASS S: actual RETURN supersedes stale SENT; inventory unchanged |
| 35 Missing RETURN / physical ON | PASS S: explicit review, no fake refill/availability |
| 36 Earlier verification pending | PASS S/P/B: source action remains independent |
| 37 Sort rollover | PASS S: old assignment cannot activate in new operation |
| 38 Duplicate submit/verify/activation | PASS S/P: one receipt, child and posted event per visit |

Additional evidence includes out-of-order verification, source correction racing
verification, baseline racing valid source posting, atomic rollback, existing
defuel/sump, FOB, holds, tail-swap, follow-up, vendor/inventory, PROF and rolldown
regressions. No write-based test uses a production aircraft or Google workbook.

## Baseline failures, limits and release gate

Untouched archive of starting `9d095f4`:

* Both existing Dispatch profile browsers fail the same color assertion:
  `rgb(240, 209, 122)` versus `rgb(213, 216, 222)`. Not changed in this task.
* Seven existing `neoscorpion_dispatch_workflow.test.js` failures reproduce on
  starting HEAD (old source-string/scroll harness contracts). No assertions removed.
* Two older vendor PostgreSQL fixtures call Dispatcher COMPLETE without Fueler
  submission. Their failure is the required safety rejection, not permission to
  bypass submission. New continuation PostgreSQL tests exercise the valid sequence.

Correction affecting an already finalized descendant requires authorized historical
review, not an automatic rewrite. Unknown legacy physical timing stays unqualified.
Existing v3/v4 PROF and historical fueling evidence are not recalculated.

The latest requested operational-safety confirmation remains a production release
gate. No production aircraft mutation, push or manual deploy is authorized by a
passing test alone. Exact commit/Render status must be recorded after that gate.

## Recorded local validation

* Affected operational Python gate: **151 passed** (28 continuation cases included).
* Additional SQLite personal-profile tests: **6 passed**.
* New continuation PostgreSQL coverage: **34 passed** (32-case run plus two
  subsequently added boundary cases). Includes duplicate submit/verification,
  competing Dispatcher edits, correction-versus-verification, valid-submission
  baseline-versus-posting, rollback, append-only visits and repeat bootstrap.
* Existing lifecycle/preference PostgreSQL coverage: **9 passed**. Six vendor
  PostgreSQL tests also passed; the two old missing-submission fixtures failed
  identically on the untouched starting-HEAD archive (as described above).
* Focused JS: **20 passed**. Seven separate legacy JS failures reproduced on
  starting HEAD and remain reported, not excluded to imply an all-suite pass.
* Real multi-truck browser test: **PASS Chromium and WebKit**, separate authenticated
  desktop Dispatcher/mobile Fueler contexts, actual ON/OFF/save/submit/verify controls,
  three simultaneous review actions, dirty other row, open dialog, source correction,
  no duplicate child card, independent verification, preferences and live updates.
  Widths 1920, 1440, 390 and 375: no document overflow; mobile five visible columns
  and no visible mutation submits. Desktop primary row/input alignment checked.
* Existing completion-scroll browser: **PASS** in both sort modes plus mobile live
  completion. Normal refresh, expanded DETAILS and working-area retention preserved.
* Compilation, both modified JavaScript syntax checks, and `git diff --check`: PASS.
  Python warning/error logs from intentional injected-failure tests are expected;
  test assertions passed. The two existing arrival-color browser failures are baseline.

No production mutation was used for certification. At this local validation checkpoint,
origin/main remained `9d095f4705a595bc48d877644fa976f4643934c4`; no new push/deploy occurred.

## Release handoff

Changed files for this implementation:

* `app/models/neoscorpion_dispatch_preference.py`
* `app/models/neoscorpion_fuel.py`
* `app/models/neoscorpion_pending_uplift.py`
* `app/neonodes/neoscorpion/routes.py`
* `app/services/neoscorpion.py`
* `app/services/neoscorpion_dispatch_preferences.py`
* `app/services/neoscorpion_pending_uplift.py`
* `app/services/neoscorpion_schema.py`
* `app/services/neoscorpion_spear_optimizer.py`
* `app/services/neoscorpion_spear_replanning.py`
* `app/services/neoscorpion_submission.py`
* `app/services/schema_sync.py`
* `app/static/css/26-neoscorpion.css`
* `app/static/js/neoscorpion_fuel_data.js`
* `app/static/js/neoscorpion_fuel_dispatch_live.js`
* `app/templates/base.html`
* `app/templates/neonodes/neoscorpion/_dispatch_action_center.html`
* `app/templates/neonodes/neoscorpion/_dispatch_mobile_details.html`
* `app/templates/neonodes/neoscorpion/_dispatch_plan_form.html`
* `app/templates/neonodes/neoscorpion/_dispatch_work_timeline.html`
* `app/templates/neonodes/neoscorpion/_fuel_dispatch_panel.html`
* `app/templates/neonodes/neoscorpion/_fueler_card.html`
* `app/templates/neonodes/neoscorpion/fuel_dispatch.html`
* `app/templates/neonodes/neoscorpion/fueler.html`
* `docs/audits/2026-10-16-neoscorpion-multi-truck.md`
* `tests/browser/test_neoscorpion_continuations.py`
* `tests/js/neoscorpion_cancel_unassign.test.js`
* `tests/js/neoscorpion_dispatch_collapse.test.js`
* `tests/test_neoscorpion_continuations.py`
* `tests/test_neoscorpion_continuations_postgres.py`
* `tests/test_neoscorpion_dispatch_preferences.py`
* `tests/test_neoscorpion_fueler_submission.py`
* `tests/test_neoscorpion_lifecycle_audit.py`
* `tests/test_neoscorpion_spear_optimizer.py`

Continue the preserved, validated main implementation. Do not rebuild or rerun a
broad test marathon. Obtain the outstanding fresh no-active-operation confirmation,
fetch origin/main, inspect any concurrent changes, review/stage only this change set
(exclude the NeoRain prototype and untracked artwork), and commit/push main only.
Render autoDeploy must pick up that exact commit. Verify successful bootstrap, exact
LIVE SHA, `/healthz` HTTP 200 and the versioned Scorpion assets. Never manually deploy
or write artificial production fueling transactions. If upstream changes, integrate
safely and rerun only affected gates. Report baseline failures separately.
