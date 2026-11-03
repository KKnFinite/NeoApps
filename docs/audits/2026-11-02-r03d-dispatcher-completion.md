# R03D — eight legacy Dispatcher completion regressions

## Decision and source

**The eight-case completion scope is verified. The wider R03 backlog remains open.**

Fetched `origin/main`; main was already synchronized at
`867dd06e50d7d23e16943f6c4d91b4802f03dfc2`. Render independently reported
that commit LIVE in `dep-dbkk6ds9v7es73cnjltg` before this work.
There were no tracked edits. Six unrelated untracked artwork, prototype and
documentation items, and all ignored synthetic PostgreSQL files, were preserved.
No applicable AGENTS.md was found in the inspected repository instructions.

The R03C report/register, R03B physical-fueling report, original R03/R08 evidence
and current canonical lifecycle implementation were inspected. All eight original
IDs failed again before edits. Seven stopped at `Assign the truck used for
positive T/F before OFF.` The UNKNOWN case expected a movement error but instead
hit the missing Fueler handoff guard. Neither result exercised its intended
downstream integrity assertions.

**No new application defect was established. This release changes tests and
audit documentation only.** No permission, inventory calculation, completion
guard, operational behavior, schema, configuration, asset or archive changes
are included. The final SHA and AutoDeploy receipt for the commit containing
this report are checked after push and supplied in the completion response;
the read-only deployment receipts are retained under ignored `instance/r03d`.

## Verified contract

The authoritative five stages remain assignment → physical TRUCK ON → physical
TRUCK OFF → Fueler COMPLETE ASSIGNMENT → Dispatcher COMPLETE. The October 16
[lifecycle contract](2026-10-16-neoscorpion-lifecycle.md) and current services
agree. Data entry alone is not physical ON or a Fueler submission.

The rebuilt helper creates synthetic operation/mission/resource records, assigns
the truck through `save_dispatch_assignment()`, calls
`confirm_fueler_truck_on()`, saves the actual readings/APU/T/F with
`save_fueler_entry()`, calls `mark_fueler_off()`, and submits using the current
`fueler_context()` edit baseline. It never sets completion, physical ON/OFF,
submission, inventory deduction or event fields to manufacture eligibility.

Truckless FOB remains a separate explicit Dispatcher workflow with fresh Actual,
APU and adequate net fuel. It creates no physical ON/OFF or transfer event.
A real truck visit with equal measured fuel and zero T/F is tested separately
through ON/OFF and handoff; it also creates no transfer event or deduction.

The current uplift inventory contract permits recording genuine completed work
when an old recorded truck balance is unknown or insufficient. It preserves that
balance, marks it unverified, and records a discrepancy audit rather than creating
a fictional balance. This is implemented by `_close_current_truck_segment()` in
`app/services/neoscorpion.py` (uplift reliability branch near 5380 and discrepancy
audit near 5446). DEFUEL has its own verified capacity protection; it is unchanged.
The old assertion that every unknown/insufficient uplift balance must block
aircraft completion conflicts with this established contract.

Two explicit adverse legacy-balance injections are limited to inventory metadata
after a fully genuine physical visit/handoff. The unknown balance uses a valid
TOPPING OFF asset state to preserve the database constraint. No physical fact,
receipt or authorization is injected. These cases prove that completion neither
floors inventory nor invents gallons. TOPPING OFF/OOS exceptions are exercised
by changing assets through canonical services **after** physical OFF, never by
letting an unavailable truck begin work.

## Per-case evidence

Every original method name is retained. The machine-readable
[completion evidence](2026-11-02-r03d-completion-evidence.json) records the
original failure, final SQLite/PostgreSQL result and meaningful assertions.

| Original method | Root cause and correction | Integrity assertions actually reached |
| --- | --- | --- |
| `test_active_fueler_filter_and_read_only_dispatch_render` | Old preparation claimed positive movement without its truck/ON/handoff. Rebuilt completed and OFF-only visits. | Completed work leaves the active Fueler list; OFF-only work remains. Dispatch GET commits zero times and preserves event, work, both truck balances and revision. Read-only controls are absent. |
| `test_existing_event_and_fuel_on_board_block_normal_complete` | Old fixture inserted an orphan/incomplete event and then deleted it. It did not represent real completion. | Canonical completion creates one immutable event. Repeated completion is a no-op with one deduction. A separate explicit FOB is rejected by normal COMPLETE. No historical event is deleted or rewritten. |
| `test_moved_completion_validates_truck_tf_and_gallons` | Initial missing-truck guard masked T/F and inventory assertions; old unknown/low-stock rejection expectations were outdated. | No truck rejects OFF. Missing/zero T/F rejects handoff and completion. Canonical roster removal rejects posting. Unknown/low stock remains unchanged and unverified with the exact discrepancy audit; repeated completion cannot post again. |
| `test_moved_success_creates_event_deducts_once_and_preserves_master` | Missing physical prerequisites prevented success path. | One event owns the exact operation/assignment/work/tail/truck and real timestamps. 75 gallons deducts 400→325 once. Starting stock and master-truck reading stay unchanged. Master Schedule and mission identity remain unchanged. Event/tank snapshots survive retry and rejected later Fueler edits. |
| `test_not_moved_completes_without_event_or_truck_deduction` | Old no-transfer fixture invented positive T/F to force OFF. | Explicit truckless FOB uses fresh evidence with no ON/OFF/event/deduction. A separate genuine no-transfer truck visit passes ON/OFF/handoff and normal completion with zero events/deductions. |
| `test_stale_cross_sort_attempt_and_route_permission_do_not_complete` | Invalid physical setup prevented reaching sort and authority checks. | Valid submitted work rejects stale sort, foreign gateway, Fueler direct completion POST, absent CSRF and stale Work ID. AJAX conflict is 409 with the actual `X-Requested-With` contract. No event, completion or inventory change occurs. |
| `test_topping_off_and_oos_nightly_trucks_may_complete_moved_work` | Old fixture tried to assert post-OFF asset exceptions without genuine physical work. | Truck is available at real ON/OFF/handoff. Canonical subsequent TOP OFF SENT/OOS retains the released visit; 10 gallons posts once and asset status remains unchanged. |
| `test_unknown_movement_blocks_completion` | Raw ORM milestones plus incomplete tanks expected the later movement error, bypassing current handoff requirements. | Canonical ON/OFF with Remaining-only evidence classifies UNKNOWN. Missing Actual blocks handoff/completion; no receipt, event, completion, deduction or revision change is invented. The test intentionally does not bypass handoff to reach a later guard. |

An additional rollback test injects an exception immediately **after** the real
event/inventory posting function has run. Caller rollback removes the event,
restores inventory and revision, and preserves the already committed Fueler
receipt. A subsequent legitimate completion succeeds exactly once. PostgreSQL
also starts two independent completion transactions together and verifies one
winner, one no-op, one event and exactly one stock deduction.

No original test was deleted, skipped or marked expected-failure. Intermediate
fixture errors (unknown stock on an AVAILABLE row and a missing AJAX header)
were corrected to the actual database/route contracts, not accepted as looser
status assertions or used to justify application changes.

## Validation

Existing virtual-environment dependencies were reused. Synthetic SQLite and
loopback PostgreSQL fixtures used the existing external-socket guard. Browser
tests use isolated data and mocked providers, not production aircraft.

| Run | Measured result |
| --- | --- |
| Starting completion module | 8 failed, 3 passed; 4,022 warnings; 7.09 s |
| Final completion module, including one-commit route and added rollback test | 12 passed; 6 passing subtests; 5,139 warnings; 9.30 s |
| R03B/R03C scope plus related lifecycle, submission, continuation, physical evidence and completion | 162 passed; 45 passing subtests; 65,404 warnings; 120.39 s |
| PostgreSQL completion plus existing lifecycle/submission/physical evidence/R03C coverage | 45 passed; 11 passing subtests; 20,385 warnings; 142.64 s |
| Final PostgreSQL completion fixture and explicit concurrent stock-deduction check | 11 passed; 4,843 warnings; 32.13 s |
| Chromium/WebKit Dispatcher/Fueler workflows | 3 methods passed, each running both engines; 3,356 warnings; 149.38 s |
| Affected JavaScript tests | 18 passed, zero failed/skipped |
| Compilation, actual lifecycle JavaScript syntax and whitespace | Both changed Python modules compile; both actual JavaScript files parse; `git diff --check` passes |

The 162-case run preceded the final helper improvement to make truck assignment
itself pass through the canonical Dispatcher service. The final 12-case and
PostgreSQL receipts cover that helper. Existing independent concurrency/visit
tests were not edited. The final completion suite includes the previously
repaired `test_complete_route_commits_once` unchanged.
Across the two PostgreSQL runs there are 46 distinct passing methods; ten
completion methods were rerun after the final fixture improvement, not counted
as additional unique tests.

The browser methods cover real ON/OFF/submission/Dispatcher verification,
source-specific earlier visits across canonical A→B→A tail swaps, independent
events/inventory, and two accounts with a dirty row/open dialog while COMPLETE
eligibility updates. This is six engine/workflow combinations, not six separate
pytest methods. Existing SQLAlchemy and deprecated-UTC warnings were retained,
not globally suppressed. An initial syntax command named a nonexistent
standalone cancel script; the test actually targets Dispatch JavaScript. The
correct two existing files were then explicitly checked successfully.

## Original R03 inventory and remaining risks

The original 367-ID inventory is rerun sequentially with the established bounded
Windows harness: 2 GiB owned-process-tree limit, 4 GiB workstation headroom,
20-minute deadline, external providers blocked. This is a regression inventory,
not a load test. The only existing renamed R03A ID remains explicitly mapped.

The final measured inventory is **56 passing and 311 failing original IDs**,
compared with R03C's **48 passing and 319 failing**. Exactly these eight newly
pass; no previously passing original ID regressed. All 367 have executed results,
with no skips, missing IDs or collection errors. Pytest reported 600 failure
events, 88 passing outcomes and 356 passing subtests in 250.63 seconds; these are
not 600 distinct broken cases or 88 wholly passing original IDs. The monitored
test tree peaked at 874,811,392 bytes (834.3 MiB); the resource limits were not
reached. Original per-ID results are distinguished from pytest subtest and
failure-event totals. The updated
[backlog register](2026-11-02-r03d-backlog-register.json) retains all original IDs
and earlier diagnostic/classification provenance.

No full-system green claim is made. Remaining failures are material verification
gaps, not automatically proven production defects and not automatically obsolete
tests. Next recommended scope: a separately bounded investigation of the remaining
NeoScorpion completed-event correction/physical-start cases. Rebuild current
canonical prerequisites, preserve correction authority and original-event
immutability, then investigate later assertions. NeoErmac mission identity,
Google source authority, attendance permissions and query-budget clusters remain
separate follow-up scopes.
Specifically, the seven remaining IDs in `test_neoscorpion_fuel_corrections.py`
still stop at the missing-truck physical OFF guard. Their correction/reopen and
post-OFF editing expectations need independent contract verification; this
release does not assume those later assertions are correct or harmless.

## Changed files and production boundary

Only these five files are part of the release:

* `tests/test_neoscorpion_dispatcher_complete.py`
* `tests/test_neoscorpion_dispatcher_complete_postgres.py`
* `docs/audits/2026-11-02-r03d-dispatcher-completion.md`
* `docs/audits/2026-11-02-r03d-completion-evidence.json`
* `docs/audits/2026-11-02-r03d-backlog-register.json`

The disposable PostgreSQL server is stopped cleanly, and its files are retained.
No temporary-directory deletion is attempted. No production fueling, inventory,
mission, truck, employee, permission, archive or Google workbook test mutation is
performed. No historical repair, provider test write, hosting/configuration
change, schema change or unrelated-node change is included. Production
verification uses read-only public probes, the existing authenticated session
and existing Render AutoDeploy monitoring.
