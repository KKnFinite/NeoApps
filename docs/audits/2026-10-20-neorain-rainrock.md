# Rainrock approved dashboard — 2026-10-20

Starting main: `5283128bb3e0b382301dded078313bb8f0dcbba3`.

## Data authority and persistence

- The top SORT INPUTS editor uses the existing versioned `NeoRainReportEntry`
  table and Daily Briefing's transaction/first-insert locking contract.
- HPS/Actual Volume use `planned_volume` / `actual_volume` in both reports.
- Ramp/Hub Payroll use `briefing_*_staffing_planned`; Worked uses
  `briefing_*_staffing_actual`. Daily Briefing retains Actual/Planned ordering.
- Older Rainrock staffing entries remain intact and are read-only fallbacks
  until a canonical shared input is explicitly entered. No historical migration
  copies, deletes, or rewrites those facts.
- Smalls, Planned FPH, Actual FPH and notes retain their existing field names.
  Actual FPH is manual only. Zero and missing values remain distinct.
- Notes 2–5 from the former editor remain visible in the finished report.
- No Google writeback code or Google workbook is modified.

## Default Planned FPH

One additive column on `neorain_operational_settings`:
`default_planned_fph INTEGER NOT NULL DEFAULT 75000`.

Existing SQLite/PostgreSQL bootstrap mechanisms add it idempotently. The
canonical sort factory snapshots the gateway's current default as that new
sort's existing `planned_fph` report entry. Changing Settings never rewrites
older sorts or per-sort overrides. Older sorts without evidence display a dash.

## UI, live updates and PNG

The editable inputs are outside the captured report. Three summaries and
six-column inbound/outbound tables use the existing mission identities,
scheduled/actual times, inclusion rules and lateness calculations. Every saved
delay is visible in saved order, including early/excluded flights. Missing
actuals are identified rather than fabricated.

Current reports poll normally; historical reports do not initiate current-sort
Google refreshes. Input drafts, focus and base versions survive report updates.
Conflicts retain the draft with an explicit field-local USE SAVED recovery.

Pinned, locally served html2canvas 1.4.1 (MIT) captures a stable 1200px report at
2x where browser limits permit. Clipboard receives PNG, never substituted text.
Clipboard failure/unsupported browsers download the same complete PNG. Safe
canvas limits reduce resolution or reject an oversized capture explicitly;
they never silently crop a report. Inputs/navigation/buttons are not captured.

## Validation evidence

- 70 focused Python tests: Rainrock, Daily Briefing, reports, settings, sort
  creation, overrides and report missing/zero-value presentation.
- 8 disposable local PostgreSQL tests: shared canonical facts, concurrent
  Rainrock/Briefing first insert, stale saves, defaults/history and repeat-run
  additive migration. No production database writes were used for testing.
- 20 JavaScript checks: autosave, draft/version retention, stale responses,
  conflicts, readonly access, complete-image behavior and Briefing regressions.
- Chromium and WebKit browser workflows at 1920×1080, 1440×900, 390×844 and
  375×568: actual input saves, independent sessions, shared values, conflicts,
  horizontal flight rows, mobile internal scrolling, no page overflow.
- Chromium actual clipboard PNG readback; WebKit's automation environment
  rejects native clipboard writing and successfully downloads the PNG fallback.
- A 114-flight synthetic report including full delay notes exports at
  2400×14286. Desktop/mobile exports have identical complete dimensions.
- Daily Briefing's actual Chromium text clipboard equals its displayed output.
- Local screenshots, PNGs and measurements are in ignored
  `instance/browser-evidence/rainrock-dashboard/`.

The existing broad `test_neorain_reports_browser` reaches its unrelated Inbound
override section after the report checks, then times out looking for
`data-rain-override-open`. Current starting-main `_override_controls.html`
already has direct inputs and no such button. That obsolete non-Rainrock
assertion was not weakened and unrelated operational controls were not changed.

Python compilation, JavaScript syntax and `git diff --check` are release gates.
Production checks are read-only; exact GitHub/Render SHA and public asset hashes
must be verified after AutoDeploy before reporting LIVE.
