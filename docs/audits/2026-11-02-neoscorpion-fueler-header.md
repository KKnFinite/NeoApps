# NeoScorpion Fueler shared-header verification — 2026-11-02

Starting main/origin and production SHA: `0a59d883fc248461f37d452b17aefdd76b9c95bf`.

This is a presentation-only release. The canonical shared card now reads **TAIL · FLIGHT · GATEWAY ORIGIN · DESTINATION** on one line. ETD replaces the old Tail grid cell. The separate destination subheading and upper-right ETD are removed. Normal Fueler and Dispatcher OPEN retain Fueler / ETD / Parking as their first information row; direct entry retains its existing mode-specific hidden fields. All remaining information fields and operational controls are unchanged.

## Data binding and sizing

`_fuel_rows()` supplies only one new read-only presentation value, `gateway_code`, from the existing `SortDateOperation.gateway_code`. It introduces no lookup, write, route change or mission-origin substitution. Tests distinguish an RFD operation from a mission whose origin is SDF and verify an alternate ONT operation-code projection.

`_fueler_card.html` remains the single shared template. `26-neoscorpion.css` owns the layout; its integrity-manifest digest and dedicated cache version are updated. A separate `neoscorpion_fuel_header.js` measures the complete identifier line after initial rendering, dynamic dialog/live-card insertion and font loading. CSS container units respond immediately during viewport resizing. ResizeObserver/window-resize fallback supports resizing, and detached headers are released. There are no timers, network calls, storage writes, form handlers or operational mutations in this helper.

Preferred type is 36px, reduced proportionately when necessary. Text is never wrapped, ellipsized, clipped or truncated. There is no minimum font size that could force overflow; unusually long identifiers necessarily use smaller type.

## Browser measurements

Chromium and WebKit tested 320, 375, 390, 430, 768, 1024 and 1440px in Normal Fueler, Dispatcher OPEN and ENTER AND COMPLETE. Both normal and longer identifiers were checked, followed by a reopened dialog. There are 112 recorded geometry samples (including reopened-dialog repeats), with no header overlap, wrapping, clipping or page-level overflow.

Representative normal Fueler line geometry:

| Viewport | Available width | Chromium font / line height | WebKit font / line height |
| --- | ---: | ---: | ---: |
| 320px | 244px | 15.33px / 17.62px | 15.33px / 17.62px |
| 390px | 314px | 19.73px / 22.69px | 19.73px / 22.67px |
| 1440px | 1080px | 36px / 41.39px | 36px / 41.39px |

Long example: `RA-12345 · UPS12345 · RJAA · ZBAA`. Minimum measured type across the tested modes/widths was 12.65px. All complete values remained visible.

The earlier user-supplied annotated Fueler image was inspected as the reference. Fresh screenshots below use isolated synthetic data, not production aircraft or personnel.

| Width | Before | After |
| --- | --- | --- |
| Mobile 390px | [Before](assets/fueler-header/chromium-fueler-before-390.png) | [After](assets/fueler-header/chromium-fueler-after-390.png) |
| Desktop 1440px | [Before](assets/fueler-header/chromium-fueler-before-1440.png) | [After](assets/fueler-header/chromium-fueler-after-1440.png) |

## Verification results

- Curated NeoScorpion core: **299 passed / 1 failed out of 300**, identically before and after. The pre-existing failure is `OperationalTimelineTest.test_same_tail_pending_actuals_survive_a_new_visit_and_stale_form`: it expects HTTP 400, but the existing stale-work guard returns HTTP 409. No fueling or stale-work behavior was changed to satisfy it.
- Focused shared-header/source and CSS integrity: **6 passed**.
- All NeoScorpion JavaScript regressions: **95 passed**, including six new presentation-helper checks for sizing, dynamic insertion/removal, hidden-dialog activation, fonts/text changes, fallback and immediate container resizing.
- New Chromium/WebKit header matrix: **2 passed**, exercising every requested viewport and all three modes, grid labels, preserved drafts and direct-entry calculations/eligibility.
- Existing shared-screen browser lifecycle: **2 passed** on the starting UI and **2 passed** on the final UI using a local-only fixture adjustment setting `SESSION_COOKIE_SECURE=False` for HTTP loopback. The unadjusted WebKit harness cannot retain its secure session cookie on local HTTP; production cookie configuration is unchanged.
- Existing Fueler redesign/lifecycle browser test: **1 passed**, covering Chromium and WebKit. The unadjusted shared test initially caught an immediate-resize overflow; synchronous container sizing corrected it, and the same test passed afterward.
- Curated PostgreSQL batch: **54 passed, 1 failed, 1 error out of 56**. The failure is the same pre-existing 409-versus-400 assertion, independently reproduced with the starting `_fuel_rows` implementation. The error was the isolated test cluster's 20-connection limit in `test_simultaneous_settings_accept_one_baseline_and_preserve_winner`; that case passed alone on both starting and final code. Across the batch plus the explicit isolated rerun, 55 distinct PostgreSQL cases passed and the existing assertion mismatch remains visible. No production PostgreSQL was used.
- Python compilation, new JavaScript syntax check and `git diff --check`: passed.

The existing shared-screen browser exercise includes normal Fueler data display, Dispatcher read-only physical controls, actual direct-entry completion, failed/stale response draft retention and live completion visibility. The Fueler workspace browser test exercises physical fueling workflow. No production test mutations were performed.

## Scope and limitations

No changes to fueling lifecycle, ON/OFF, submission/completion, APU, T/F, calculations, permissions, inventory, autosave, source ownership, schema, worker configuration or operational APIs. No historical repair, Google write or archive write. Existing unrelated untracked files/artwork are excluded. The disposable PostgreSQL service was stopped and its synthetic files preserved.

The two existing HTTP-status assertions and the local browser/cumulative PostgreSQL harness limitations above are documented rather than silently modified. This focused release does not claim the wider R03 backlog is green.

Release confirmation (exact delivery SHA, Render deployment ID, authenticated read-only checks and deployed asset digests) is recorded after AutoDeploy in the completion response and local `instance/fuel-header/live-*.json` receipts.
