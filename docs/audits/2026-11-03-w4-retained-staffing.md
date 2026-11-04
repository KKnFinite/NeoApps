# W4 — Retained staffing records and current authority

## Result and scope

**D3 is corrected. All 24 original W4 IDs pass.** Current employee/work-area authority selects retained node timecards; the slice's original work area remains historical attribution. No historical rows are relocated or rewritten by the correction.

Starting `main` and fetched `origin/main`: **`b5d5312a6ae4d5de600ba4e9c312def9bb5fc161`**. The observed pre-release Render deployment was `dep-dbl6lbnb9u1s73fo03g0`, LIVE at that SHA. Final release identity is reported after AutoDeploy verification; it cannot be embedded self-referentially in this commit. Local release receipts are under ignored `instance/w4`.

This implements [the final plan's W4 package](2026-11-03-final-remediation-plan.md), not the other four packages. The [machine-readable evidence](2026-11-03-w4-evidence.json) maps every assigned original ID to its starting failure, verified cause and final result, and lists the eight remaining failures in broader parent modules. The full 367-ID inventory was deliberately **not rerun**; its last whole-inventory measurement remains 78 passing / 289 failing. Do not infer a new whole-inventory total from these package results.

## Confirmed defect and canonical reproduction

Before correction, the canonical EBM → WBM reproduction failed on unchanged application code. A real attendance slice was retained for the peer on September 10 Night with `call_in`, using `sync_attendance(..., as_of=2026-09-12)`. The actual `assign_work_area(peer, areas['wbm'])` transferred the employee. Both EBM and WBM weekly node GETs returned 200, but neither displayed that retained slice. Its database record and source EBM context were intact. Removing only the historical area filter from an otherwise authorized read exposed the row: authority already permitted the employee/Sort; the extra attribution predicate hid it.

`read_rows()` and `report_context()` combined two different concepts:

1. `authorization()` established current active employee and authorized Sort membership from management/node scope and Home assignments.
2. Node `work_area_ids` then filtered `Slice.work_area_unit_id`, which describes **where the historical attendance occurred**, not where the employee is currently managed.

After a transfer those predicates contradicted each other. The weekly adapter also ignored an explicit requested week and imposed the selected operation's Sort name over already authorized Combo halves.

The new canonical tests verify both node views after transfer: EBM no longer selects the transferred employee; WBM displays the retained row with its original EBM label, attendance, operation ID, Sort ID and context. The actual UI transfer was also exercised through the shared employee editor in Chromium and WebKit, followed by prior-week timecard editing in WBM.

## Application correction and authority contract

Only two application files change:

* `app/services/neostaffing_timecards.py`: requested node work areas narrow the actual current authority before resolving employees and authorized person/Sort pairs. They cannot expand authority. Node reads/reports no longer re-filter those authorized slices by historical area. **Central historical work-area report filters retain their original meaning and implementation.**
* `app/services/neostaffing_timecard_ui.py`: weekly views honor a valid explicit ISO `date` and reject invalid dates with HTTP 400. They use the existing authorized person/Sort union rather than an additional operation Sort-name restriction. Day views still select the exact operation. The weekly range remains Sunday–Saturday and bounded to seven days.

Existing authority is preserved, not replaced with a role fallback:

* Node workspaces require the active linked management identity, current Home/area and existing approved node/edit permission. A revoked node grant, inactive manager or denied action permission cannot gain retained history through an employee's transfer.
* Central Staffing retains its actual active-leadership authority. Node permission is intentionally a separate established contract: revoking central leadership alone does not revoke an otherwise valid node/edit grant. Conversely, a role alone does not authorize attendance writes.
* Combo access requires the configured employee/Sort relationship and an actually authorized current half. An unrelated historical Sort stays denied. Arbitrary requested area IDs cannot broaden access.
* Writes retain the existing employee-parent lock, week lock, current-authority recheck, expected versions, attendance gating and transaction rollback. No new write path, lock, permission cache, polling or schema is introduced.

GET verification records **zero INSERT/UPDATE/DELETE statements and zero commits** for the transfer's EBM/WBM time views and WBM report. Snapshot comparisons include slices, segments, edit audit and week receipts. Historical area/context, attendance and Sort identity remain unchanged after legitimate time edits too.

## Shared test repairs

The 24 original cases were reproduced before editing: **0 passing / 24 failing**. Repairs were consolidated around established contracts:

* Three attendance fixtures now create an active linked management actor with canonical leadership assignment instead of calling authorized attendance writers with `None` or an unlinked recorder. Existing status, uniqueness, foreign-area and historical snapshot assertions execute.
* Eight CSRF cases had nine exact-tag parsers that rejected the valid shared meta token's renewal attributes. One semantic `csrf_token()` helper replaces those parsers. Existing missing-token rejection, real POST, authority, stale-version, capacity and rollback assertions remain.
* Shared scope-link/editor helpers verify the actual selected area and canonical employee-editor fragment, rather than retired inline detail markup or fixed query-string ordering. Date validation and exact candidate membership remain asserted.
* Retained-view tests no longer forbid an internal current-roster read needed to resolve authority. They prohibit writes/commits and verify actual retained rows, old labels, non-HERE attendance, scope and week boundaries.
* The PT-Master test's original name is retained for inventory continuity. Its old blanket leadership-assignment denial contradicted current `management.assign` and the separately protected Reports To workflow. It now reaches both: one permitted leadership assignment, denied direct Reports To change, and zero reporting relationships. No production permission code changes. This separation was already documented in `2026-09-11-neostaffing-accountability.md` and is enforced by the current routes/services.

No tests are deleted, marked expected-failure, blanket-skipped or made green by changing operational rules. Existing datetime/ORM warnings remain visible.

## Validation

All mutations used synthetic local accounts, isolated SQLite/PostgreSQL and mocked providers. The disposable PostgreSQL server was stopped after use; its existing synthetic files were preserved.

| Run | Observed result | Scope |
| --- | --- | --- |
| Exact original W4 IDs, before | **24 failed**, 16.52 s | Baseline rejection/assertion capture |
| Exact original W4 IDs, final | **24 passed**, 16.32 s | Every assigned ID reached meaningful downstream assertions |
| Focused retained-scope + existing timecards | **28 passed**, 18.44 s | Eight new contracts plus 20 existing timecard methods |
| Ten affected SQLite parent modules | **293 passed / 8 failed**, 93 passing subtests, 149.04 s | Includes the 28 above; do not double count them |
| Disposable PostgreSQL | **33 passed / 0 failed / 0 skipped**, 81.40 s | Nine retained-scope methods (including transfer/edit race) plus 24 existing PostgreSQL timecard methods |
| Real browser workflow | **1 method passed**, both Chromium and WebKit, 16.88 s | Each engine exercises desktop 1440 and mobile 390, prior-week transfer/autosave, stale edit and revocation |
| JavaScript | **12 Node runner tests passed**, zero failures/skips | Two workflow-script entries plus ten node-attendance/People/assignment/vacation tests; the script entries include six archive/timecard checks and node-autosave contracts |
| Compilation / syntax / diff | Passed | Changed Python compilation, affected JS syntax and `git diff --check` |

PostgreSQL coverage includes independent-transaction lost-update rejection, bulk all-or-nothing behavior, archive acknowledgement races and attendance/edit serialization. The new transfer race holds the canonical employee lock while moving EBM → WBM; an old-area edit rechecks current scope and rolls back, then a WBM edit succeeds. Existing export/retention tests cover complete CSV/XLSX output, non-HERE and outside-scope export, bounded purge, calendar-month retention, archive acknowledgements, no-op versions and Combo aggregation.

Browser evidence uses real login/CSRF/CSP and the actual employee editor. A September 3 retained EBM row is saved as 22:00–02:00, transferred to WBM, removed from EBM's selection and shown in WBM with the same EBM attribution and four hours. A later valid save succeeds. A second stale tab receives **409** while retaining its unsaved `04:00` draft. Deactivating the manager makes the next node GET **403**. Both engines recorded zero page errors. Screenshots were inspected and remain locally at:

* `instance/w4/browser/chromium-before-transfer.png`
* `instance/w4/browser/chromium-after-transfer-1440.png`
* `instance/w4/browser/chromium-after-transfer-390.png`
* `instance/w4/browser/webkit-before-transfer.png`
* `instance/w4/browser/webkit-after-transfer-1440.png`
* `instance/w4/browser/webkit-after-transfer-390.png`

Weekly date selection is a bounded read parameter; the operational Sort selector and attendance's current-operation rules are not redesigned. Selecting an arbitrary old date does not create attendance, restore expired data, or grant another Sort.

## Remaining failures and next package

**W4 assigned originals: 24 passing, none remaining.** No new failing ID appeared in the executed parent modules: all eight failures below were already recorded in R03J and assigned elsewhere in the final plan. Their current failures remain visible; they are not claimed harmless or fixed here.

| Owner | Parent-module failures still present |
| --- | --- |
| W5 | `test_approved_neostaffing_user_can_open_landing_menu`; `test_landing_hides_destinations_when_existing_thresholds_deny_them`; `test_people_interaction_script_contracts_remain_parse_separated`; `test_staffing_dashboard_sidebar_and_mobile_menu_share_ordered_destinations` — navigation/retired inline-script expectations |
| W2 | `test_grandmaster_calendar_admin_and_context_queries_are_bounded` — 23 SELECTs against budget 17; `test_union_context_uses_bounded_queries_as_calendar_count_grows` — 14 against 9. Attribute real reads before changing budgets. |
| W5 | `test_current_cleanup_mobile_and_time_only_contracts` — retired `type="time"` assertion; `test_dashboard_order_and_normal_half_width_live_counts` — changed navigation ordering |

Recommended next scope: **W2**, the plan's other independently confirmed application defect D1 (public-asset request boundary), together with attributed SQL budgets. Then execute the remaining W1/W3/W5 contract packages and one final full-inventory reconciliation. No claim of a full-system green suite or production Neon savings is made.

## Changed files and safety

Application: the two services named above.

Tests/helpers: `tests/html_contracts.py`, `tests/neostaffing_authority_fixture.py`, `tests/neostaffing_contracts.py`, `tests/test_neostaffing_attendance_counts.py`, `tests/test_neostaffing_data_foundation.py`, `tests/test_neostaffing_discipline.py`, `tests/test_neostaffing_management_review.py`, `tests/test_neostaffing_management_vacation_picks.py`, `tests/test_neostaffing_routes.py`, `tests/test_neostaffing_vacation_selection.py`, `tests/test_staffing_live_ux.py`, `tests/test_neostaffing_retained_scope.py`, `tests/test_neostaffing_retained_scope_postgres.py`, `tests/browser/test_neostaffing_retained_timecards.py`.

Documentation: this report and `docs/audits/2026-11-03-w4-evidence.json`.

There is no migration, historical repair, production employee/timecard mutation, new account/access grant, Google write, archive change, hosting configuration change or asset modification. Unrelated untracked artwork, NeoRain prototype files, audit notes and ignored synthetic PostgreSQL directories remain intact and unstaged. Production verification is limited to deployment identity, startup/logs, health/login, authorized read-only pages and public asset identity. A short rollout observation cannot establish the absence of every future application error.
