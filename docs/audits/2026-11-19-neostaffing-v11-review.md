# NeoStaffing V11 review

Built locally on `design/neostaffing-v11` from fetched `origin/main` at `1ead37a9bba11c9cc16b3e8cfc26da1227cd5946`. The corrected package's four instruction files, reference HTML and all 22 screenshots informed the implementation. The workspace uses the package's navy surfaces, blue-gray outlines, teal selection, amber exceptions, compact panels and responsive navigation.

The implementation commit is `66e85899dbc1faed334a5bb37b52fb2f7343f18f`. The visual review then caught a clipped mobile reporting tree. The final review commit includes the stacked tree correction and a browser assertion that the child card fits in the chart.

## Screen checklist

| Reference | Implementation | Verification |
| --- | --- | --- |
| 01 Sort Overview | `/neostaffing`, `v11_overview.html` | Unique assigned and HERE totals, attendance exceptions, scope coverage and permission-aware shortcuts. |
| 02 Attendance | `/neostaffing/attendance`, `attendance.html` | Complete status choices, separate HERE and assigned counts, status autosave and authorized All Here. Existing notes stay in storage and out of the page, response and signed V11 snapshot. |
| 03 General Staffing Board | `/neostaffing/board`, `v11_board.html` | Other-operation area distribution, empty areas, genuine HC targets, one Shift Flow entry, confirmed initial placement and permanent transfers. Temporary within-Sort staffing moves require a separate product contract. |
| 04 Specialized Shift Flow | Existing `/neostaffing/shift-flow` | Protected baseline. No layout or skin changes. See the captured discrepancy below. |
| 05 People | `/neostaffing/people`, `people.html` | Search, filters, roster cards, inspector, canonical editor and management/employee creation. Existing direct employee editor links still open. |
| 06 Management Reporting | `/neostaffing/org-chart`, `org_chart.html` | Actual Reports To tree, name search, expandable branches, desktop zoom and authorized reviewed changes with revision checks. |
| 07 Operational Structure | `/neostaffing/org-chart?view=structure` | Separate hierarchy and inspector, existing scoped edit and headcount permissions, and stale-version rejection on V11 edit forms. |
| 08 Daily Area Staffing | `/neostaffing/reports?report_type=staffing` | Retained actual attendance marks grouped by the recorded area and date. Headcount coverage and original classification limitations are visible. |
| 09 Employee Attendance History | `/neostaffing/reports?report_type=attendance` | Free-text employee search, date and classification filters, retained date/status rows, no notes. |
| 10 Seniority | `/neostaffing/reports?report_type=seniority` | Canonical ranked rows and classification filter. Legacy vacation and union exports remain on their own endpoints. |
| 11 Menu | `/neostaffing/menu`, `v11_menu.html` | Six primary links where authorized. Desktop sidebar, five-slot mobile dock, collapsed More Tools and active secondary selection. |

## Shift Flow hard stop

Before changing code, Chromium rendered current main with synthetic personnel at widths of 390, 430 and 1440 pixels. The mobile first pages showed three and four Final Door columns. The desktop showed 12. All three rendered West and East Ballmat groups with five Ballmat names and zero per-door Ballmat anchors. Paging changed the visible door range and retained the same group memberships. The reference requires a vertical Ballmat list aligned under each corresponding Final Door and paged with it.

The specialized page, its map/template, its CSS, its employee colors and its roster/paging script were preserved. The shared editor's V11 transfer-preview action runs only when dispatched by the new general Board. The legacy Shift Flow asset URL and navigation remain in use there. A decision on the geometry is required before work on Shift Flow can resume.

## Historical source and privacy

- `StaffingWorkAssignment` records active placement and effective dates. It is mutable and cannot prove the complete assigned roster for a historical Sort.
- Rollover purges individual `StaffingDailyAttendance` rows under the existing retention rules. `StaffingAttendanceSummary` preserves department/operation totals. Those totals cannot recover individual statuses or work-area staffing. Accountability occurrences cover selected absence outcomes and cannot fill the gaps.
- Retained timecards carry recorded date, status and original area. Reports read those records alongside still-retained daily attendance, keyed by Sort Date Operation and employee. Current authorized areas govern report access. For named attendance history, a non-Master must have active management authority over the recorded area. A later transfer does not substitute the employee's new area.
- Newly created timecard snapshots record classification at the time the mark is captured in their existing context JSON. Older snapshots are not backfilled. A classification-specific daily report includes records with evidence of that classification.
- The general Board shows current assignments. Daily Area Staffing shows recorded historical headcounts only. Unmarked historical workers, omitted records and detail past both retention windows cannot be recovered. Complete dated staffing rosters would need an approved capture and retention policy. This branch does not introduce a new record store or change purge schedules.

## Review evidence

PNG captures for every reference ID were taken with synthetic records in Chromium at 390, 430 and 1440 pixels, plus WebKit at 390 and 1440 pixels. Raw screenshots and the pre-edit Shift Flow baselines are under `instance/browser-evidence/neostaffing-v11/`. Screenshot identities and headcounts are fixtures. Layout differences in the reports reflect the historical records that are actually available.

| Check | Result |
| --- | --- |
| Focused SQLite and browser suites | 68 passed, plus 74 subtests. Covers status-only autosave, All Here, authorization, retained area attribution, historical classification, Sort identity, desktop/mobile rendering, and the canonical transfer and directory services. |
| Final visual and interaction pass | Seven passed, plus 60 subtests across Chromium and WebKit. Covers all 11 references at the supported review sizes, the complete visible mobile reporting tree and a confirmed initial Board placement. |
| Selected JavaScript suites | 39 passed. Also checked syntax of all changed JavaScript files. |
| Isolated PostgreSQL transfer, hierarchy and retained record suites | 34 passed, one failed. The retained timecard concurrency fixture hit a SQLAlchemy detached `User` in canonical access control. The same case and exception reproduced on untouched main. |
| Protected Shift Flow browser suite | Three passed and five failed on both the branch and the detached base. The V11 screen matrix separately verified door counts, the unchanged West/East groups and the exact door-assignment color. |
| Older NeoStaffing route, permissions and reporting assertions | 58 passed, 40 failure entries, plus 33 passing subtests. Many assertions still target the retired launcher, duplicated scope rail, old report families and older row markup. This legacy suite remains open for contract-by-contract reconciliation before release. |
| Python compilation and `git diff --check` | Passed. |

The branch uses additive fields in existing timecard JSON for future classification evidence and needs no database schema migration. No production writes, production migrations, pushes, merges, issues or deployments were performed.

## File inventory

- Routes and services: `app/neostaffing/routes.py`, `app/services/neostaffing.py`, `app/services/neostaffing_timecards.py`, `app/services/neostaffing_workspace.py`.
- Templates: `app/templates/base.html` and `app/templates/neostaffing/` files `_employee_editor_mount.html`, `_navigation_items.html`, `attendance.html`, `org_chart.html`, `people.html`, `v11_board.html`, `v11_menu.html`, `v11_overview.html`, `v11_reports.html` and `v11_shell.html`.
- Assets: `app/static/css/neostaffing_v11.css` and `app/static/js/` files `neostaffing_attendance.js`, `neostaffing_employee_editor.js`, `neostaffing_org_layout.js`, `neostaffing_people_search.js`, `neostaffing_v11_board.js` and `neostaffing_v11_shell.js`.
- Tests: `tests/test_neostaffing_daily_attendance.py`, `tests/test_neostaffing_v11_workspace.py` and `tests/browser/test_neostaffing_v11_workspace.py`.
