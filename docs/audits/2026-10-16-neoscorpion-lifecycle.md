# NeoScorpion lifecycle audit and release gates

Audit baseline: `4b54b4ea0bf525a2efeeed2f926e25014affa08b` (fetched and
fast-forwarded main before inspection). Unrelated NeoRain prototype/artwork preserved.
This document is an evidence ledger, not a blanket claim of operational certification.
The October 16 consolidated requirements supersede older automatic ON/OFF/completion
statements in `docs/neoscorpion-physical-fueling-and-prof.md`.

## Transition/dependency map (recorded before production-code changes)

All mutations use authenticated gateway/node permission checks, the existing
operation/asset/assignment locks, and caller commit/rollback. Rejected mutations
must preserve data and return a specific error; stale identities require refresh,
not silent redirection to another visit. Work = append-only FuelWorkState;
Event = immutable FuelingEvent + tank snapshots; revision = nightly asset state.

| Flow / UI trigger | Route / service | Entities and preconditions | Transaction, audit, revision / expected next UI |
| --- | --- | --- | --- |
| ASSIGN / UPDATE | `/fuel-dispatch/assignment`; `save_dispatch_assignment` | Current operation/mission, eligible resources; active physical work cannot be relabeled | Locked assignment/resource change; fuel audits + revision. Both resources = ASSIGNED immediately; partial = READY with missing resource. No physical ON. |
| Save Fuel Entry / APU APPLY | `/fueler/assignments/<id>`; `save_fueler_entry` | Explicit visit/cycle identity and field baselines; valid tank/APU values; post-OFF editing allowed | Locked Work/tanks/T/F; changed-field audit + revision. Save data only, never create physical ON/OFF. Material corrections revoke submission; notes do not. |
| TRUCK ON | `/fueler/assignments/<id>/on`; `confirm_fueler_truck_on` | Assigned Fueler/truck; current physical visit; no conflict/hold/OOS/TOP OFF/sump; actual arrival may override predicted travel only | Server-time idempotent physical ON, provenance, audit + revision. Fueler FUELING, explicit OFF enabled. |
| TRUCK OFF | `/fueler/assignments/<id>/off`; `mark_fueler_off` | Trusted ON, correct physical visit and operational access, no contradictory occupation; final data not required | Physical OFF and actor; inventory usage unresolved/unverified as applicable; audit/revision. Truck released, mission OFF; final inputs still editable. |
| COMPLETE ASSIGNMENT | `/fueler/assignments/<id>/submit`; `submit_fueler_assignment`, `neoscorpion_submission.state/evidence` | Explicit ON then OFF, saved complete tank/APU/required/T/F/direction data, exact visit; no mandatory review | Submission actor/time/fingerprint/version + audit/revision committed atomically. No OFF creation, transfer posting, or administrative COMPLETE. Fueler receives durable submission confirmation; Dispatcher COMPLETE eligible. |
| Dispatcher COMPLETE | `/fuel-dispatch/complete`; `complete_fueled_assignment` | Current valid submission and physical ON/OFF; direction, event and inventory protections | Event/inventory posting once; completion actor/time/audit + revision. Cycle COMPLETE; pending uplift promotion attempted in a savepoint. Duplicate completion is no-op. |
| START UPLIFT / DEFUEL | `/fuel-dispatch/start-follow-up`; `start_follow_up_fuel_cycle` | Completed parent, verified same-tail measurement, valid requested target/resources; no conflicting pending cycle | Freeze display + exact rollback; fresh Work/cycle. Previous Actual → Remaining only. Clear Actual/T/F/APU/notes/ON/OFF/submission. Audit + revision. Uplift/defuel must complete their own five stages. |
| Add/edit PENDING UPLIFT | `/fuel-dispatch/pending-uplift`; `save_pending_uplift` | Current parent identity, pending optimistic version, valid target/planned resources | Pending row/audit/revision only. No active readings/physical reservations/posting. Changes do not revoke unrelated parent submission. |
| Promote pending / RETRY | `promote_after_completion` / `_attempt_promotion` | Dispatcher completed parent; net NEO target still requires uplift; exact final parent measurement; resources revalidated | Savepoint protects completed parent/event. One fresh cycle or persistent REVIEW with exact reason. RETRY/CANCEL preserve parent history. |
| CANCEL UPLIFT / DEFUEL | `/fuel-dispatch/cancel-uplift`, `/cancel-defuel`; `_cancel_follow_up_cycle` | No positive current-cycle T/F, persisted transfer, or ambiguous mandatory transfer review; exact rollback available | Restore prior operational projection, retain canceled Work/ON/OFF/tanks/audit; no fake fuel reversal. Release reservations without fabricated OFF. Revision. |
| Tail change / advisory acknowledgment | Google/MotherBrain canonical mission ingestion; `/fuel-dispatch/confirm-tail`; `confirm_assignment_tail` | Effective tail distinct from proposed swap; canonical mission/link identity; acknowledgment is not physical clearance | Old physical tail/visit stays immutable. New tail gets separate preparation/visit and zero inherited T/F. Only verified matching-tail measurement can seed Remaining. Audit + shared mission fingerprint/revision. |
| Prior-tail / previous-visit recovery | Explicit Work ID/sequence query on Fueler/Dispatch APIs; `_locked_source_segment`, `_segment_views` | Exact owned segment; no choosing by tail alone | OFF/data/submission/finalization act on source Work. Never complete replacement mission; retain actionable unresolved source work. |
| Truck/Fueler swap, UNASSIGN, NEW VISIT | Existing resource/lifecycle routes; swap/unassign services and `begin_physical_visit` | Correct physical identity; no discarded active physical evidence; truthful closure | Retain prior Work/Event/inventory. New visit allocates permanent new ID, including A→B→A. No invented OFF or copied T/F. Revision + resource audits. |
| FOB (no truck/no transfer) | `/fuel-dispatch/fuel-on-board`; existing FOB assessment/service | Complete measured or trusted same-tail evidence, valid APU/net fuel; no physical transfer disguised as FOB | Explicit Dispatcher FOB decision, no fabricated truck ON/OFF or transfer event; preserved measurement provenance and revision. |
| DEFUEL recovery / VERIFY READY | Existing mark-sumped inventory action | Authorized new confirmed reading and capacity/service checks | Explicit readiness/inventory audit; physical OFF alone never clears needs_sump. |

## Synchronization contract and audit targets

Revision endpoints must observe committed operation/asset revision and canonical
mission/tail fingerprint. Fueler success is issued only after transaction commit.
Dispatch must patch eligible COMPLETE actions and precise blockers even while
another row is dirty or Fueler Data is open. Patches must carry matching mission,
cycle and Work identity and reject stale/out-of-order responses. Full replacement
still preserves unsaved values, focus, DETAILS, and scroll. Sort rollover must not
attach old controls to a new operation. Background polls remain read-only.

Baseline defects reproduced and corrected:

- `submit_fueler_assignment` calls physical OFF implicitly (contradicts explicit order).
- `save_fueler_entry` still creates ON for the truckless FOB-check branch.
- `neoscorpion_fuel_data.js` blocks submission on any dirty card but writes feedback
  only to the earlier card status; generic HTTP 200 handling labels saves "Saved".
- Critical Dispatch patch selects DETAILS by mission group alone (history can be first)
  and clones COMPLETE without validating target form's cycle/Work identity.
- Assigned status can remain PENDING behind missing arrival/fuel/parking prerequisites.
- Duplicate/stale submission, follow-up cancellation, multi-visit finalization,
  pending resource protection, and immutable submission invalidation now have
  direct service/transaction tests listed below.

## Incident evidence and limitations

Read-only Render access logs identify assignment 489 and explicit
`physical_work_id=440&physical_sequence=1` on October 16:

- 07:01:33.419Z (02:01:33 CDT): `POST .../489/on`, HTTP 200.
- 07:01:38.735Z (02:01:38 CDT): `POST .../489/off?...440...1`, HTTP 200.
- 07:11:06.699Z (02:11:06 CDT): `POST .../489/submit?...440...1`, HTTP 200.
- A later, DIFFERENT visit 446/sequence 2 has data saves at 07:32:28/30Z,
  ON 07:32:32Z, OFF 07:32:33Z, data 07:32:40Z, submission 07:32:48Z and
  another data save at 07:33:12Z. Do not merge this with visit 440.

The reported N168UP / UPS0556 / MSP / C07 identity is supplied by the incident
report; the unavailable database connector prevents independently confirming
the original row contents, actor, evidence hash or subsequent invalidation.

HTTP 200 does not prove which fields committed or whether a submission fingerprint
was valid. The available Neon connector reports an unavailable tool and Render
lists no hosted PostgreSQL instances (the production database is external).
Do not infer actor, transaction evidence, inventory, or Dispatcher verification
from neighboring anonymous access-log entries. Additional correlated logs and
isolated reproductions are recorded below as the audit progresses.

## Confirmed root causes and repairs

| File / function | Expected vs. reproduced baseline behavior | Repair / proof |
| --- | --- | --- |
| `neoscorpion.py:submit_fueler_assignment` | Submission must require explicit OFF; baseline manufactured OFF during submission | Removed implicit OFF. Exact ON/OFF, tank, APU, required-fuel and T/F blockers; service tests and real Fueler controls reject incomplete physical sequence. |
| `neoscorpion.py:save_fueler_entry`, `_attach_fueling_start_state` | Preparation must not start physical work; truckless FOB save could create ON | Removed save-time ON and FOB ON promotion. Preparation and truckless FOB tests assert no physical milestones. Persisted trustworthy historical timestamps remain intact. |
| `neoscorpion.py:_retire_visit` | Resource changes must not invent OFF | Physically ON swaps/unassignment reject with an explicit OFF instruction. Old visit remains addressable. |
| `neoscorpion_fuel_data.js` submit/feedback | Dirty mobile fields must give visible, actionable rejection; baseline warning was above viewport | Feedback beside COMPLETE ASSIGNMENT. Real mobile click verifies no saved submission and visible warning. Successful submission requires a durable backend receipt, not generic 200/Saved. |
| `routes.py:_fuel_data_request` | Success must mean a committed submission | Receipt emitted after commit with Work/cycle/sequence/actor/time/version; correlated accepted/rejected logs. Duplicate unchanged submission retains one audit and original receipt. |
| `neoscorpion_fuel_dispatch_live.js:applyCriticalCompletion` | New eligibility must reach the correct current visit despite another dirty row/dialog | Independent server-rendered verification form, strict operation/mission/cycle/tail/Work/resource identity, exact current DETAILS lookup, clean-mission-only group reconciliation. Never submit another row's draft or attach a new visit to an old form. |
| `routes.py:fuel_dispatch_live_panel`, `fuel_dispatch_context` | Critical handoff must not wait for optional planning enrichment | Read-only `completion_only=1` core context runs first; full SPEAR-rich refresh remains separate. Test deliberately makes optional planner raise if invoked on core path. |
| Dispatch/Fueler live JS | Older GET must not replace a newer committed action | Generation/revision/operation checks reject stale responses; JS tests and two-context browser handoff. |
| `neoscorpion.py:complete_fueled_assignment` | Dispatcher must approve exactly the submission displayed | Current visit/cycle/tail/submission-version checks; source visits use explicit Work/sequence and source submission version. PostgreSQL serializes correction/submission/verification. |
| `neoscorpion_fueling_status.py:fueling_status` | Both resources mean ASSIGNED immediately | Removed unrelated readiness prerequisites from assignment stage; missing resources give precise READY detail. Physical timing remains independent. |
| `neoscorpion.py:confirm_assignment_tail`, inherited-measurement validation | Replacement may inherit only matching, completed same-tail Actual as Remaining | Work-scoped provenance audit; fresh Actual/APU/ON/OFF/T/F/submission; no old-tail measurement or inferred APU promoted to proof. |
| `neoscorpion.py:_cancel_follow_up_cycle` | Cancel all untransferred visits in the canceled cycle, preserving facts | Closes reservations for every unfinalized cycle visit, retains ON/OFF/tanks/audits, restores completed parent, no fuel reversal. Positive transfer anywhere blocks cancellation. |
| `neoscorpion.py:_record_defuel_service_requirement`, `_close_current_truck_segment` | OFF releases occupation, not defuel service restrictions | Proven received DEFUEL fuel marks needs_sump at OFF or later data save, without early posting. Unresolved defuel has explicit recovery-review gate. Authorized sumping after OFF remains effective through later administrative posting. |
| Optional SPEAR decision-scope call sites | Valid first/truck-only changes must tolerate no asset revision row | Use revision zero when absent; no planning rule changes. |

These isolated reproductions explain how an operator could believe a click
succeeded without an accepted submission, or how an accepted submission could
remain unavailable in a held/stale Dispatch UI. They do NOT establish which
mechanism occurred in assignment 489: its original database evidence and actual
Dispatcher screen observation were not accessible. New correlation logging
supports that distinction for future incidents. No historical rows were repaired
by guessing, and no live fueling transaction was used as a test.

## Major workflow outcomes

PASS below means the named isolated coverage passed, not that every possible
browser/production combination is exhaustively certified.

| Flow | Result | Evidence / qualification |
| --- | --- | --- |
| Normal fueling | FIXED | Explicit five stages, exact blockers, durable receipt, two-account mobile/desktop handoff under 10.5-second assertion. |
| Additional uplift | PASS | Three consecutive fresh cycles, immutable prior events, carried Remaining only, independent posting. |
| Pending uplift | PASS | Create/edit while active, no premature promotion, net-NEO target review, retry/cancel/savepoint preservation; actual browser workflow and real PostgreSQL concurrent edit/approval test. |
| Defuel | FIXED | Direction/capacity tests, explicit handoff, received-fuel serviceability at OFF, explicit sumping, no double posting after new reading. |
| Cancel uplift / defuel | FIXED | Preparing/ON/OFF with saved tank/APU/zero-T/F data, preserves physical facts and prior event/balance. Any prior positive visit blocks cancel. |
| Tail swap before ON / after OFF / after submission | FIXED | Dedicated phase matrix preserves source evidence and keeps replacement incomplete; same-tail verified inheritance test. |
| Tail swap during ON / after COMPLETE | PASS | Browser A→B→A through canonical MotherBrain swap, independent visits; source OFF/data/submission/explicit verification; no replacement completion. |
| Multiple physical visits | PASS | Same/different trucks, earlier visit pending Actuals, immutable original 3,000 gallons, new 2,000 gallons, independent B transfer; PostgreSQL and browser. |
| Unassign / reassignment | FIXED | Pre-ON preparation preserved, active ON cannot be silently closed. After OFF a new physical visit is the supported resource-handoff path; direct Fueler swap still uses existing correction guard. |
| FOB-only | FIXED | No invented truck/ON/OFF; positive T/F/physical evidence rejected; fresh APU with trusted same-tail measurement. |

## Validation evidence

Local only: SQLite isolated fixtures, disposable PostgreSQL on localhost port
55440, and isolated Flask/Playwright servers. Operational Google and production
missions were not mutated.

| Suite | Result |
| --- | --- |
| Core lifecycle + submission + pending uplift + FOB + uplift/defuel | 62 passed on final expanded rerun |
| Vendor workflow + Rolldown + resource-PROF integration + pending uplift | 59 passed; expected synthetic missing-evidence log is an injected test fixture |
| Google/MotherBrain live missions | 60 passed; effective vs. proposed tail, CANX, correlation, no-op timestamp stability, master isolation and per-row failures |
| PostgreSQL segments + lifecycle + submission | 26 passed on final rerun (including expanded cancellation-data assertions); additional pending-edit/approval concurrency test passed: 27 checks total |
| Focused JS: critical completion, fuel data, sort hotfix, scroll, cancellation | 16 passed; three modified scripts pass `node --check` |
| Chromium + WebKit submission | Real basic sequence and separate-account dirty-other-row/open-dialog handoff passed |
| Chromium + WebKit pending uplift and completion scroll | Passed; both sort modes, preserved drafts/DETAILS and working viewport |
| Chromium + WebKit A→B→A visit workflow | Passed; actual Fueler controls, MotherBrain swap, dispatcher acknowledgment and original visit verification |
| Chromium + WebKit sort rollover | Passed; real polling replaces old-operation missions and controls within 10 seconds, with new operation identity |
| UTC/DST/source END EARLY/legacy review | Targeted checks passed; revision query-budget test remains a confirmed baseline failure (below) |
| Compilation / whitespace | `compileall` and `git diff --check` passed; LF/CRLF Git notices are not whitespace failures |
| NeoRain reports / overrides | 30 passed; no implementation edits to NeoRain |
| NeoErmac live integrity | Combined 39-test cross-node run: 8 failures, 1 error; identical counts and failing cases on untouched HEAD, not introduced by Scorpion edits |

Browser handoff uses two independent authenticated accounts/contexts, 1440×900
Dispatch and 390×844 mobile, and checks 98px/73px rows, overflow, console errors,
dirty input retention, OFF then submission then explicit Dispatcher action.
Not every scenario below was separately driven through both browsers.

### Baseline failures (not concealed)

An untouched `git archive` of starting HEAD was run in an ignored local snapshot:

- Five legacy suites: 151 tests, 18 failures and 31 errors on production HEAD.
- Legacy safety/inventory group: 91 tests, 10 failures and 12 errors on HEAD.
- Many fixtures predate mandatory submission and expect automatic ON/OFF or
  event posting on a save; these are not the current consolidated contract.
  Only directly reused fixtures were updated to execute actual submission and
  explicit Dispatcher approval, never to manufacture evidence.
- `test_fuel_dispatch_revision_active_operation_is_read_only_and_bounded`:
  10 SELECTs vs. budget 8 on both HEAD and this draft. No poll writes were observed.
- `test_finished_assignments_do_not_block`: old expectation releases Fueler at
  OFF without proven final work; same failure on HEAD. This audit did not weaken
  resource-availability uncertainty to satisfy the obsolete expectation.
- The 123-test adjacent current run had 122 passes and the latter baseline
  failure. Do not describe the entire legacy suite as green.
- The cross-node NeoErmac baseline failures concern lineup slot/status contracts
  and an arrival-boundary fixture; all reproduced on untouched HEAD. They were
  not repaired by changing unrelated NeoErmac code.

Logs remain ignored under `instance/lifecycle-*.log`; do not commit database
fixtures, generated browser evidence or the untouched baseline archive.

## Scenario-by-scenario ledger

Evidence keys: **L** = `test_neoscorpion_lifecycle_audit`; **S** =
`test_neoscorpion_fueler_submission`; **P** = `test_neoscorpion_pending_uplift`;
**U** = `test_neoscorpion_uplift_defuel`; **F** = `test_neoscorpion_fuel_on_board`;
**G** = `test_google_motherbrain_live_missions`; **PG** = the three PostgreSQL
suites above; **B** = real browser submission/visits/pending/scroll tests;
**J** = focused JS tests. A PASS at service layer does not imply a separate UI
test for that exact scenario.

| # | Scenario | Result and actual evidence |
| --- | --- | --- |
| 1 | Normal five stages | FIXED — S explicit OFF/submission; B actual controls and two accounts. |
| 2 | OFF before ON | PASS — S cannot invent ON; L exact blockers; legacy unsafe ON rejected. |
| 3 | Submit before OFF | FIXED — S `requires_separate_physical_off`; B disabled control. |
| 4 | Missing Remaining | FIXED — L `exact_missing_tank_and_apu_blockers_saved_at_off`, names LEFT/CTR/RIGHT. |
| 5 | Missing Actual | FIXED — same L test, exact missing tanks. |
| 6 | Missing APU | PASS — S missing APU/T/F/hold/actor and L blockers. |
| 7 | Data before ON | PASS — S preparation/unassign; L tail-before-ON matrix; no inferred ON. |
| 8 | Data after OFF | PASS — S OFF without Actual, B late Actual submission. |
| 9 | OFF with incomplete data | PASS — S/L/B, occupancy ends without posting/completion. |
| 10 | Saved valid fields enable submit | PASS — L stepwise blockers; B actual controls. |
| 11 | Durable submission | FIXED — L HTTP receipt re-read from DB; PG. |
| 12 | Dispatcher already viewing | FIXED — B normal polling assertion under 10.5s. |
| 13 | Dispatcher edits another row | FIXED — B dirty-other-row remains intact through COMPLETE. |
| 14 | Fueler Data dialog open | FIXED — B open-dialog handoff, close then verify. |
| 15 | Mobile dirty fields | FIXED — B actual rejected click, visible adjacent warning, no saved receipt. |
| 16 | Duplicate submission | PASS — L exact same baseline retains one receipt/audit; PG. |
| 17 | Stale submission | PASS — S stale baseline after correction; PG correction/submission conflict. |
| 18 | Correction after submission | PASS — S revokes material data/target, optional note preserves; B correction. |
| 19 | Duplicate Dispatcher COMPLETE | PASS — L repeat no-op; PG concurrent one event/posting. |
| 20 | Additional uplift | PASS — U, L repeated followups. |
| 21 | Previous Actual → Remaining | PASS — U and L immutable source snapshots. |
| 22 | Fresh uplift Actual blank | PASS — L repeated followups; P promotion. |
| 23 | Fresh APU/ON/OFF/T/F/notes/submission | PASS — L repeated followups. |
| 24 | Three followups | PASS — L cycles 2–4, PG alias. |
| 25 | Pending while active | PASS — P edit-while-ON and B pending controls. |
| 26 | Edit pending resources/target | PASS — P optimistic version + B repeated edits. |
| 27 | No promotion on Fueler submit | PASS — P/B; only explicit Dispatcher service promotes. |
| 28 | Atomic pending promotion | PASS — P single promotion/savepoint, B and PG simultaneous pending edit/Dispatcher approval; one promoted cycle/event with accepted or rejected stale edit. |
| 29 | Net NEO already meets pending target | PASS — P target equality/net APU cases, no fabricated transfer. |
| 30 | Pending RETRY/CANCEL | PASS — P unavailable resource then retry; stale/cancel, B actual controls. |
| 31 | Defuel five stages | PASS — U/L, real service sequence; no separate two-browser defuel test. |
| 32 | Opposite movement | PASS — U direction/capacity rollback. |
| 33 | Defuel inventory exactly once | PASS — U and PG posting; correction-epoch coverage in vendor group. |
| 34 | Defuel truck not dispatchable | FIXED — L needs_sump at OFF, unresolved recovery gate, no fake occupation. |
| 35 | Explicit sump recovery | PASS — U confirmed gallon requirement; L sump before late admin posting. |
| 36 | Cancel uplift before movement | FIXED — L populated setup/ON/OFF zero-T/F matrix. |
| 37 | Cancel defuel before movement | FIXED — same L matrix, prior history/balance retained. |
| 38 | Cancel after ON/no transfer | PASS — L physical ON preserved, reservations closed without fabricated OFF. |
| 39 | Cancel after OFF/no transfer | PASS — L factual OFF/readings retained. |
| 40 | Cancel positive transfer | PASS — L earlier-visit T/F blocks despite blank current projection. |
| 41 | Effective Google swap before ON | PASS — G ingestion; L preparation-phase replacement. |
| 42 | Swap during ON | PASS — L original source; B actual swap and busy-truck protection. |
| 43 | Swap after OFF before submit | PASS — L dedicated tail phase matrix. |
| 44 | Swap after submission | PASS — L source receipt preserved, replacement not approved. |
| 45 | Swap after Dispatcher COMPLETE | PASS — B A→B→A and L frozen original. |
| 46 | Same-tail inheritance only | FIXED — L verified matching Remaining + fresh APU; unrelated tail not copied. |
| 47 | Proposed vs. effective tail | PASS — G pending swap not effective until actual effective tail changes. |
| 48 | MotherBrain manual swap | PASS — B canonical route with actual entity version + CSRF, not direct SQL mutation. |
| 49 | CANX not aircraft | PASS — G cancellation marker preserves mission and never creates tail. |
| 50 | Repeated visits / A→B→A | PASS — L/PG/B independent permanent IDs, tanks, gallons, timestamps. |
| 51 | Unassign before work | PASS — S truck-only/preparation paths, no ghost ON/T/F. |
| 52 | Unassign after work begins | FIXED — L active ON rejected, existing visit preserved; post-OFF new visit is separate. |
| 53 | Separate Fuelers/tails | PASS — ownership checks in S and vendor group; no exhaustive simultaneous browser pair of two active Fuelers. |
| 54 | Concurrent sessions | PASS — PG correction/submission, duplicate ON/OFF/verification and rollback. |
| 55 | No-truck FOB | FIXED — F complete without ON/OFF, repeat no-op. |
| 56 | No truck + positive T/F | PASS — F explicit rejection. |
| 57 | Trusted same-tail FOB | FIXED — L same-tail inheritance, fresh APU, no fabricated truck milestones. |
| 58 | Sort rollover polling | PASS — P stale operation rejected, J cross-sort response rejection; B actual Chromium/WebKit new-sort polling removes old mission controls and adopts new operation. |
| 59 | Reload between milestones | PASS — B canonical reloads/state persistence and scroll refresh; no production write. |
| 60 | Out-of-order responses | FIXED — J generation/revision/cross-sort checks, Fueler action invalidates older GET. |
| 61 | Truck released/admin pending | PASS — L/B distinct OFF/submitted/complete; PG posting once later. |
| 62 | Pending uplift while TOP OFF | PASS at service layer — P `unavailable_resource_retains_completed_parent_then_correct_retry` explicitly sets planned truck to topping_off; no premature activation. No separate two-browser TOP OFF combination. |
| 63 | Target changed during fueling | PASS — L changed target rejects old Fueler screen; S correction+revert still resubmits. |
| 64 | Notes across cycles/swaps | PASS — L fresh follow-up notes, source Work retained; S optional notes don't invalidate valid submission. |
| 65 | Repeated destination labels | PASS at service layer — L `repeated_destination_labels_never_select_another_mission_visit`: different mission/tail/Fueler readings remain independent with identical destinations. No separate XXX1/XXX2 browser scenario. |

## Integrity, scope and remaining limitations

- No new schema/model migration. Existing additive bootstrap and foreign keys are
  regression-tested; historical Work IDs/events/snapshots stay owned by the same
  physical visit. No historical ON/OFF/gallons rewritten by this patch.
- One transfer event/inventory posting per segment; existing inventory epochs,
  unknown/unverified balances and discrepancy approvals retained. Pending/denied
  readings are not promoted into applied fuel.
- Tank planning, balancing, APU formulas, density, PROF arithmetic and SPEAR
  recommendation priorities were not changed. Unreliable legacy performance
  stays excluded, not made qualifying to satisfy old fixtures.
- No production incident-database reconstruction, exhaustive 65-scenario browser
  certification, Google workbook mutation or active production fueling test was
  performed. Per-scenario browser coverage limits above remain explicit; do not
  equate service/transaction coverage with 65 separate two-browser workflows.
- No NeoRain/NeoErmac/MotherBrain implementation edits. G tests cover their
  shared canonical mission contract; this is not a full cross-node acceptance run.
- Unrelated artwork, NeoRain prototype and protected stash contents remain
  unmodified and must not be staged.
- User reconfirmed no active sort/significant use before release. Fetch upstream
  again before push; Render AutoDeploy only. Exact SHA/LIVE and health must be
  reported from actual deployment evidence, not inferred from a Git push.
