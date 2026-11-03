# R03I — legacy ON, physical authority and truck travel

## Decision and baseline

All four scoped failures concern invalid fixtures or outdated expectations. No new application defect has been demonstrated within this scope. This release changes tests and audit documentation only. It preserves the five-stage lifecycle, explicit no-transfer FOB, resource uncertainty, historical evidence and current v4 forecast rules.

Fetched and safely synchronized main at `9a863595bec3d5819fa59d93dc5f0ec537f937f6`. Render independently reported that SHA LIVE in `dep-dbl44r95efls73cjk190`. The checkout had no tracked edits. Six unrelated untracked artwork/prototype/documentation paths and the ignored synthetic PostgreSQL directories were preserved. No applicable AGENTS.md was found in inspected repository/parent paths.

Reviewed R03H's report/register, R03G's handoff report, the October 16 lifecycle contract, canonical assignment/ON/OFF/save/submission/completion paths, permanent Work ownership, physical-start gating, status/review priorities, truck release/travel, resource timing and SPEAR forecast contracts. The unchanged four original IDs reproduced exactly their reported first failures before edits.

Principal implementation evidence: `neoscorpion.py:2853` records protected manual ON; `neoscorpion_fueling_start.py:15` and `:62` define resource gating and trust; `neoscorpion_fueling_status.py:65` defines review and `:107` prioritizes statuses; `neoscorpion_spear_forecasts.py:14` enforces qualified forecast evidence; `neoscorpion_resource_timing.py:255` explicitly preserves untrusted legacy occupation as uncertainty rather than declaring free time. These implementations are unchanged.

## Per-case evidence

| Original method in `FuelingStartHotfixTest` | Verified root cause | Final meaningful assertions |
| --- | --- | --- |
| `test_legacy_premature_on_preserved_but_never_used_for_overdue_estimate` | REVIEW is required for persisted untrusted ON. Elapsed travel and another save cannot reauthenticate that timestamp. The old hardcoded forecast formula also ignores current v4 resource-evidence requirements. | Controlled legacy raw ON conflicts with genuine prior physical history; the raw value remains preserved, untrusted/inactive and in REVIEW with the exact verification reason. Real prior ON/OFF uses canonical services. Saves do not create confirmation or an overdue estimate. Explicit manual confirmation establishes current physical arrival without rewriting raw ON. Missing prior Fueler handoff still makes performance timing unknown, for the exact recorded reason. |
| `test_manual_on_overrides_inferred_on_and_predicted_travel_not_busy_truck` | Saved readings never created the assumed physical ON. | Real busy occupation rejects ON. Canonical OFF releases it. A labeled historical lower-confidence raw timestamp is separate from current manual confirmation. Manual ON may override predicted travel, records actor/truck/cycle on the same Work, survives later saves, and prevents another mission from starting that truck. |
| `test_partial_or_truckless_non_fob_does_not_start_and_complete_valid_save_does` | Partial/complete data are preparation; the old fixture expected completed saves to start fueling. | Truckless ON rejects; partial and complete readings create no ON. Actual Dispatcher truck assignment retains the same Work. Canonical ON, OFF and committed Fueler handoff precede Dispatcher completion. Saving and submission post no event/deduction; Dispatcher verification posts one 6,000-gallon event, changes 10,000 stock to 4,000 once, and duplicate completion is a no-op. |
| `test_preassigned_truck_release_travel_and_expiry_never_auto_create_on` | The expanded busy warning intentionally explains that a selected truck remains unavailable; the later automatic-start expectation is obsolete. | Both raw `TRUCK STILL FUELING` and visible `WAITING FOR TRUCK · TRUCK STILL FUELING` are retained and checked. Busy ON rejects. Real OFF exposes Alpha-to-Bravo travel, then TRUCK ON PENDING. Expiry and identical saves create no ON. Only the actual confirmation records physical arrival; prior ON/OFF remain unchanged. |

All 14 parent method IDs remain present. AST comparison against the starting commit permits changes only to these four method bodies; the ten other parent methods, including all R03H repairs, remain unchanged. The only shared-module addition is the import of a scoped test workflow helper. No production implementation or permission guard is altered.

## Physical-start and status contracts

* Preparation, data completeness, assignment and elapsed travel do not establish ON. Planned assignments without physical evidence do not occupy a truck.
* Current ON is an explicit assigned-Fueler action for the exact operation, mission, tail, truck, cycle and Work reference. It is idempotent, records its actor and time once, and prevents simultaneous physical use of the same truck. Wrong/stale identities reject before mutation; stale Work returns HTTP 409.
* Trusted historical ON requires the established provenance checks. Raw timestamps remain preserved separately from manual confirmation. Existing trusted historical cases remain in prior R03H coverage.
* Persisted untrusted ON requires REVIEW and cannot authorize FUELING or a performance clock. Ambiguous contradictory occupation can conservatively require review before a conflicting action; it is not a license to declare the truck free. A separate test preserves rejection/rollback of OFF while such a conflict exists.
* Physical OFF releases the truck, not necessarily the Fueler or administrative assignment. Verified current-sort location and travel determine projections. Unknown origin remains unknown. Actual manual arrival can supersede travel uncertainty, while busy/unavailable resources remain protected.
* Valid ON does not manufacture missing resource-performance evidence. Current v4 timing requires qualified assignment/parking/preparation/prior-Fueler-release evidence. The old unconditional `10 + gallons / 300` expectation is inappropriate when those facts are unknown. Such a forecast remains unavailable instead of becoming a false overdue estimate.
* No-transfer FOB is unchanged. Actual, Remaining, APU, transfer/event/inventory ownership and all five completion stages remain separate.

### Fixture corrections discovered downstream

The first repaired setup attempted genuine OFF while a contradictory raw historical ON was already present. That correctly failed the existing conflicting-mission guard. The compatibility fixtures now perform actual prior release first, then explicitly introduce the historical import as legacy evidence. They do not bypass the guard or fabricate OFF. A dedicated rollback case retains the rejected conflicting-OFF scenario. Another exploratory assertion incorrectly expected an empty current-activity string while a legitimate travel projection existed; it now checks that exact projection without claiming active fueling. The forecast assertion was corrected only after inspecting its recorded v4 resource reasons.

## Additional integrity and browser coverage

Six new contracts cover read-only timing/status with zero NeoScorpion writes, one-day-expired projections without ON, unknown release location, planned-but-unused resources, wrong Work/truck/cycle/sort, unauthorized and missing-CSRF requests, rollback after the physical snapshot has been flushed, retry/idempotency and immutable prior evidence under a rejected conflicting OFF. Test snapshots include nightly revision state in addition to Work/tank/mission/assignment/audit/inventory data.

Disposable loopback PostgreSQL schemas install production guards. Two independent-transaction races prove duplicate ON records one timestamp/identity/audit, while competing missions can start the same truck only once. The loser is rejected without events or inventory deduction. Original and additional contracts also run against PostgreSQL, alongside prior R03B–H coverage. The local server is stopped and retained; no blocked directory cleanup or query cancellation is used.

The final PostgreSQL stop command exceeded its wait deadline; a subsequent read-only `pg_ctl status` confirmed no server running. No forced shutdown or cleanup retry was needed. Test result counts below refer to actual test outcomes, independently of that local shutdown wait.

The new browser method uses separate authenticated Dispatcher/Fueler contexts in Chromium and WebKit, at desktop 1440 px and mobile 390 px. Actual UI assignment and autosave leave physical ON blank. Genuine first ON blocks the next assignment's ON with the exact server rejection. Actual OFF releases the prior visit; a fresh read shows Alpha travel. Explicit next ON uses the same saved Work and actor, preserves prior OFF and creates no completed event or deduction. Screenshots are retained under `instance/r03i/browser`.

After the deliberate rejected ON, the test obtains a fresh projection because the local rejected-action feedback remains retained. This does not claim automatic error-card refresh recovery or resolve the older excluded autosave/receipt browser investigations. Previously repaired full handoff/follow-up/Driver and no-transfer FOB browser workflows are rerun separately.

## Measured validation and complete original inventory

| Execution | Measured result |
| --- | --- |
| Four unchanged original IDs | 4 failed at the reported status/save-created-ON/warning assumptions |
| Complete physical-start parent plus six additional contracts | **20 passed**, 3 passing subtests, 17.19 s; all 14 parent methods pass |
| Scoped PostgreSQL | **12 passed**, 3 passing subtests, 33.65 s; all four original IDs plus six contracts and two independent-transaction races |
| Prior R03B–H PostgreSQL | **94 passed**, 11 passing subtests, 293.84 s |
| Expanded Python regression, including truck-queue/resource-PROF coverage | **286 passed / 4 retained baseline failures**, 76 passing subtests, 203.26 s |
| Previously passing R03H selected methods | **All 243 remain passing**, zero regressions |
| New Chromium/WebKit busy/release/travel/ON browser method | Passed in both engines, 19.80 s; initial retained-error/fresh-read harness failure is recorded separately |
| Prior Chromium/WebKit handoff and truckless FOB browser methods | Both passed in both engines, 79.39 s combined; three final browser methods total across six engine workflows |
| JavaScript | **22 passed**, zero failures/skips; three shared JS files pass syntax checks |
| Compilation/diff | Five changed/new Python files compile; `git diff --check` passes |
| Full original inventory | **76 passed / 291 failed unique original IDs**, 250.22 s, all 367 executed |

The inventory improves from the measured R03H baseline of 72/295 by these four scoped IDs. **No previously passing original ID regresses.** Raw pytest outcomes are **580 failure events / 109 passing outcomes / 355 passing subtests**; these are separate from unique-ID counts. Peak monitored owned-process memory was 911,364,096 bytes (about 869 MiB), below the 2 GiB cap. No production CU/bandwidth or runtime saving is inferred.

The four remaining selected Python failures are unchanged: Hanzo identity/timing display; `test_get_never_marks_off_and_ready_card_offers_action`; `test_physical_off_does_not_require_final_tf_and_repeat_is_noop`; and the Dispatch presentation mock passing non-iterable truck visuals. No unrelated fixture, selector or application rule is modified to force them green. Existing datetime/ORM warnings remain visible. The older excluded correction/visit browser methods are not rerun or declared resolved.

The [execution evidence](2026-11-03-r03i-start-evidence.json) records initial exploratory failures separately from final executions. The [updated backlog register](2026-11-03-r03i-backlog-register.json) retains each original ID, prior classification/provenance and current measured status. The complete 367-ID inventory is rerun sequentially under the established 2 GiB owned-process-tree cap, 4 GiB workstation-headroom guard and 20-minute deadline. Unique failed methods are distinguished from pytest failure/subtest events. No test deletion, broad skip, expected-failure mark, warning suppression or operational guard change is used. No full-system pass or production resource saving is claimed.

## Remaining scope and exact files

NeoSubZero CSS/manifest mismatch, older correction/visit browser autosave/receipt failures, Hanzo timing, NeoErmac mission identity and unrelated presentation/query-budget failures remain separate. They are not silently fixed, downgraded or closed by this package. The updated register preserves their evidence. Recommended next work is a focused review of the two remaining physical-OFF presentation/eligibility cases before wider fueling presentation work; employee/permission and mission-identity groups also retain high-priority unresolved investigations.

Exactly these eight files are included:

* `tests/test_neoscorpion_fueling_start.py`
* `tests/neoscorpion_start_workflow.py`
* `tests/test_neoscorpion_start_evidence_contract.py`
* `tests/test_neoscorpion_start_evidence_postgres.py`
* `tests/browser/test_neoscorpion_start_evidence.py`
* `docs/audits/2026-11-03-r03i-legacy-on-and-travel.md`
* `docs/audits/2026-11-03-r03i-start-evidence.json`
* `docs/audits/2026-11-03-r03i-backlog-register.json`

No application code/schema, inventory rule, permission, security policy, Google authority, FROST evidence, historical archive or unrelated asset changes. No production fueling, truck movement, completion, inventory, aircraft/employee, Google or archive test writes. Unrelated files and existing synthetic database directories remain intact. Release uses main and existing Render AutoDeploy only. Exact final SHA/deployment, bootstrap/worker startup, public health/login, read-only authorized pages, asset digest and rollout checks are recorded after push under ignored `instance/r03i` and in the completion response. Dispatch is opened only after checking that browser-triggered automatic actions are disabled.
