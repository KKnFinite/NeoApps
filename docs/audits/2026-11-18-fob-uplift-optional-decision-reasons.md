# FOB → UPLIFT and optional application decision reasons

## Source and scope

Fetched origin/main at `bb4ecd9a95b294c861d926d89437fa903a0b9cd2`. Work is isolated on `codex/fob-uplift-optional-reasons`. The separate Portal fix remains preserved at `9830eb180e13e76866a3114ee928d63de7b36e05` on `codex/portal-navigation-header`; it is not included in this package. Unrelated untracked work, jobs and synthetic directories were preserved.

## Confirmed causes and corrections

`start_follow_up_fuel_cycle()` categorically rejected explicit FOB completion, then required a physical completion timestamp and a prior fueling event. `_fuel_rows()` and Dispatcher presentation independently excluded FOB from the follow-up controls. Canonical no-transfer FOB intentionally has neither physical fueling events nor a normal physical completion timestamp, so these guards prevented legitimate UPLIFT.

The existing START UPLIFT now accepts a completed, internally consistent FOB. Under the existing operation/assignment/WorkState/tank locks it verifies current aircraft/work ownership, complete Actual readings, confirmed APU, zero/blank transfer and absence of physical activity/review. It takes the baseline from that specific FOB work, without borrowing an unrelated tail measurement or requiring a fabricated event. The existing append-only cycle infrastructure allocates a separate WorkState, copies Actual into Remaining, leaves new Actual/APU/ON/OFF/submission blank, and resets current-cycle completion markers. The old work/tanks remain unchanged. Original FOB assignment timestamps/actor and all prior values are retained in the existing cycle-history rollback receipt, labeled FOB. A `segment_transfer` audit and baseline source WorkState reference record provenance. Existing resource assignment remains an explicit Dispatcher action.

Only subsequent physical UPLIFT completion posts an UPLIFT event and inventory deduction. FOB never becomes delivered gallons. The existing cancellation service restores the exact FOB state. Expected cycle/tail and existing transaction locks reject stale and duplicate starts. DEFUEL retains its physical-event prerequisites; the shared UI now gives it a separate ineligibility explanation for FOB.

The current Accounts & Access handler and dormant scoped application-decision service both required at least three reason characters. A shared validator now permits absent/blank/short explanations for approval and denial, retaining the 2,000-character bound. Both application decision forms remove `required` and `minlength`. Supplied explanations and immutable decision history remain intact; omitted explanations use the existing neutral audit receipt. Applicant/reapplication justifications, revocation reasons, protected commissioning/password/identity confirmation and all permission/review/session checks retain their guards. The pending scoped engine is not activated.

## Executed validation

| Focused run | Actual result | Evidence |
|---|---|---|
| Reproduction on unchanged source | FOB start rejected; decision form required reason | `instance/fob-uplift-reasons-reproduced.log` |
| New SQLite contracts and disposable PostgreSQL lifecycle/rollback/concurrency | **15 passed**, zero failed/skipped (7 SQLite + 8 PostgreSQL), 182.256 s | `instance/fob-uplift-reasons-postgres-final.log` |
| Related FOB evidence, normal UPLIFT/DEFUEL, application history/authority/CSRF and pending cancellation | **13 passed, 1 failed**, zero skipped, 24.011 s | `instance/fob-uplift-reasons-regression.log` |
| Independent original-main reproduction of that pending cancellation failure | Same pre-existing rejection | `instance/fob-uplift-pending-baseline.log` |
| Real desktop Dispatcher/mobile Fueler lifecycle and desktop/mobile blank decisions, Chromium + WebKit | **1 test / 2 engine subtests passed**, zero failed/skipped, 41.162 s; zero JS errors | `instance/fob-uplift-reasons-browser-release.log` |
| Python AST, Jinja parsing and git diff whitespace | Passed | Local verification commands |

Acceptance covers FOB → separate UPLIFT → resource assignment → actual TRUCK ON → Fueler readings → physical OFF → Fueler COMPLETE ASSIGNMENT → Dispatcher COMPLETE, original FOB immutability, exactly one UPLIFT event/deduction, duplicate completion, stale old-work save rejection, canceled/failed-start rollback, wrong-tail rejection, unchanged DEFUEL exclusion, simultaneous UPLIFT requests, competing application decisions, blank and supplied decision reasons, applicant justification, revocation and future scoped compatibility. PostgreSQL uses only the existing loopback disposable database and isolated synthetic schemas; no production database test writes.

The one remaining regression is `tests.test_neoscorpion_pending_uplift.PendingUpliftTest.test_cancellation_after_promotion_uses_existing_cycle_rollback`: pending-plan promotion passes a fuel-load representation rejected by the existing one-decimal K-LBS parser, leaving the plan in REVIEW before cancellation. Running the exact original origin/main `start_follow_up_fuel_cycle` function reproduces it. No parser guard or assertion was relaxed. This pre-existing pending-plan formatting issue is separate from direct START UPLIFT after FOB; no unrelated remediation was added.

Earlier failed runs remain on disk: fixture imports accidentally expanded unittest discovery, a synthetic input initially used `32` instead of canonical `32.0`, a stale-save test initially named the wrong exception, PostgreSQL fixture cleanup confused the account actor with its engine, and browser snapshots initially preceded completion of the FOB request. The final checks use the current OFF indicator and wait for the committed FOB response/completed row before recording immutable baseline evidence. Application behavior was not relaxed for these fixture corrections.

Browser evidence is in `instance/browser-evidence/fob-uplift-reasons/`: `<engine>-fueler-submitted.png`, `<engine>-uplift-completed.png`, `<engine>-optional-decisions-mobile.png` and retained fixture paths. Browser fixtures mock external providers and use local synthetic SQLite records.

## Release hold and production preservation

Read-only Neon verification at approximately 02:10 Central November 18 confirmed zero `account_authorization_states` rows: Phase 5 cutover remains pending. RFD Night is configured 21:00–05:00 Central Monday–Thursday nights. This task is occurring inside the November 17–18 active Sort. No push to AutoDeploy main or worker restart is permitted during that window. Earliest release-window recheck: **November 18, 05:00 Central / 11:00 UTC**, followed by an actual activity check before pushing.

Latest independently queried Render state remains **LIVE** at `bb4ecd9a95b294c861d926d89437fa903a0b9cd2`, deployment `dep-dbujoarbc2fs73af6cdg`. These fixes are **local only, not deployed**. No new deployment ID exists for this package. A later safe release must integrate the focused commit, preserve the separate Portal change, push main, verify exact Render SHA/ID, startup and health, and leave scoped activation pending. No deployment automation or extra job was created.

No schema changes, production fueling/account mutations, role migrations, historical cleanup, infrastructure changes, Issues or temporary CI workflows were performed.
