# Legacy UI regression audit · November 20, 2026

The eleven entries from the NeoRaiden release run all reproduced on production main `266804a32b0d46417f5c32354911ccf2f9b76923` and in an isolated, untouched checkout of `dac9ad930717cc61b723b80153163a2b4ae9561f`. The original log ran 13 methods. Its ten assertion failures and one timeout came from five methods in `tests.browser.test_mobile_drawer.MobileDrawerBrowserTest`. Several methods contain multiple engine and viewport subtests. The separate DUCC and Sektor failures are accounted for below.

## Original eleven entries

Line numbers refer to the original test file on both comparison commits. Each “both” result was reproduced independently using the original test code against each checkout. Both runs used fresh local SQLite databases, real login, a loopback HTTP server and synthetic accounts.

| # | Failed test and line | Screen / engine / CSS viewport | Original failure | Untouched baseline vs. main | Classification and correction | Retest |
|---|---|---|---|---|---|---|
| 1 | `test_chromium` → `run_engine:355` | NeoStaffing, Chromium, desktop → 390×844 | Expected visible `.neo-mobile-bottom` within 5 s. No matching element. | Same on both | Obsolete pre-V11 selector. Locate the actual mobile workspaces landmark, follow Menu and check the current link. | Pass |
| 2 | `test_webkit` → `run_engine:355` | NeoStaffing, WebKit, desktop → 390×844 | Same missing dock selector. | Same on both | Same V11 change. Verify the desktop-to-mobile transition and navigation in WebKit. | Pass |
| 3 | `test_login_hero:729` | Login, Chromium, 320×700 | PNG filename assertion returned false. The first failure stopped the viewport loop. | Same on both | Obsolete source URL. Verify the decoded login WebP, art proportions, form visibility, error handling and successful sign-in. | Pass |
| 4 | `test_portal_launcher:671` | Portal, Chromium, 320×700 | Mobile hero PNG filename assertion returned false. | Same on both | Assert decoded mobile artwork and retain the launcher geometry and interaction checks. | Pass |
| 5 | `test_portal_launcher:671` | Portal, Chromium, 390×844 | Same PNG assertion. | Same on both | Same asset fix at the taller phone viewport. | Pass |
| 6 | `test_portal_launcher:671` | Portal, Chromium, 390×760 | Same PNG assertion. | Same on both | Same asset fix at the shorter phone viewport. | Pass |
| 7 | `test_portal_launcher:671` | Portal, WebKit, 320×700 | Same PNG assertion. | Same on both | Verify the WebKit source choice and both launchers. | Pass |
| 8 | `test_portal_launcher:671` | Portal, WebKit, 390×844 | Same PNG assertion. | Same on both | Same WebKit check at the taller viewport. | Pass |
| 9 | `test_portal_launcher:671` | Portal, WebKit, 390×760 | Same PNG assertion. | Same on both | Same WebKit check at the shorter viewport. | Pass |
| 10 | `test_portal_whole_card_actions:564` | Portal → My Account, Chromium | Expected entitlement status `pending`; found `approved`. | Same on both | Obsolete request endpoint and wrong database entity. Exercise the 303 legacy link and submit the justified request in My Account. Verify the saved pending attempt, preserved inactive entitlement, hidden launcher and denied direct access. | Pass |
| 11 | `test_portal_launcher:666` | Portal desktop, Chromium, 1920×1080 | `Locator.inner_text: Timeout 8000ms exceeded` looking for `.topbar .brand`. | Same on both | Deterministic obsolete selector. Verify the actual branded Portal home link and its image. Two further cold-browser replays each timed out at 8.00 s on the old selector, found exactly one visible home link, decoded its logo and followed it successfully. | Pass |

The V11 mobile navigation lives in `app/templates/neostaffing/v11_shell.html` (introduced by `66e85899`). The artwork templates retain source names while the shared image URL helper serves the optimized variants introduced in `f5ac9cef`. The phone launcher intentionally uses `neoapps_login_mobile.webp`; the desktop launcher uses `neoapps_portal_desktop.webp`. Separate image tests verify derivative authenticity, original proportions and cache behavior. The desktop Portal home anchor is in `app/templates/auth/_portal_header.html:3` (`9830eb18`).

The old `POST /portal/request-access` redirects to `/my-account#app-access` at `app/auth/routes.py:339–343`. Following it automatically in the old test produced a final HTTP 200 without submitting an application request. The real form goes through `app/auth/accounts.py` (`my_account_request`) and the request service in `app/services/accounts_workspace.py`. The browser test now checks CSRF rejection, redirects, request history and effective access independently. A dedicated fresh multi-app login fixture also prevents another test's saved MotherBrain visit from changing the login test's expected landing screen.

## Additional DUCC and NeoSektor evidence

| Test | Original observations on both SHAs | Cause and verification |
|---|---|---|
| `UccBrowserTest.test_physical_parking_rows_and_recorded_start_in_all_views` | Alpha pad icon stayed CONFIGURED after a synthetic spray start; 10 s expectation expired. | The shared fixture disabled refresh system-wide. Its mock patched the former Sort-dependent refresh function. DUCC now uses its independent weather refresh policy (`e3cf2ff7`, `app/neonodes/neosubzero/routes.py`, `_deice_staffing_refresh`). A real five-second, screen-specific override in the test DB exercises the existing revision path. Verified recorded spray change, physical position and absence of a duplicate across Working, TV and mobile. |
| `DuccEchoPadBrowserTest.test_chromium` and `.test_webkit` | Both timed out after 30 s waiting for a real `/ucc/revision` response. | Same fixture fault. Both checks now receive real responses, update already-open Working/TV sessions, preserve the physical tail and verify pad occupancy through completion. |
| `SektorUIPassTest.test_driver_text_and_priority_visibility` | Normal 390×844 mobile timed out after 30 s looking for removed ordinal labels. Five TV cases failed exact font-scale assertions. Measured ratios at 1920×1080, 1080×1920, 720×1280, 1366×768 and 390×844 were respectively 1.667, 2.136, 2.124, 1.600 and 2.124. Test expected 1.2, except 1.0 for the smallest width. | The approved numeric-only cards and orientation-specific type predate both SHAs (`cf41ca34`, `cdf1c63f`, `9f8fd240`). Both engines now verify the exact bay order against the canonical response, visible digits, color, minimum readable heading size, containment, three-card layout and full-viewport controls. Digit ink fits even where unused font ascent/descent exceeds a container. Screenshot crops document it at normal mobile, portrait TV and landscape TV sizes. |
| `SektorUIPassTest.test_focused_screens` | An additional untouched-baseline run timed out after requesting two spotters. | The existing approved workflow keeps the request pending until the Conductor acts. Exercise the actual request and approval, and assert each wave's correct “NOT ARRIVED” message for the synthetic Sort. |
| Windows fixture teardown | One baseline replay also raised `WinError 32` while deleting its temporary SQLite file. | Intermittent fixture cleanup. The server now drains active request threads and closes its listener before SQLAlchemy disposal and temporary directory cleanup. |

## Evidence and release

| Final checks, counting each test once | Passed | Evidence |
|---|---:|---|
| Python service, authorization, image and routing tests | 27 | Image identity and caching, Portal permissions, Raiden native ULD and mission isolation, DUCC display, Sektor control safety |
| JavaScript tests | 5 | Drawer gestures, serialized refresh and retry, bay priority and STOP updates, DUCC revision/draft handling |
| Chromium/WebKit browser methods | 18 | All 11 mobile-drawer suite methods, 3 DUCC, 2 Sektor and 2 Raiden methods. Engine, viewport and state subtests are included inside these methods. |
| **Total distinct final checks** | **50** | All checks used synthetic data. |

The original nine-method reproduction ran separately on each SHA; the extra spotter-mode case also ran on the untouched baseline. These intentional reproductions, exploratory iterations and two cold-browser timeout probes are excluded from the 50. An auxiliary combined run stalled after the WebKit drawer screenshots and was stopped. A diagnostic Python process also exited with Windows status 0xC0000005 during a timed stack dump. The affected methods were rerun in smaller processes without tracing. Neither incident was needed to reproduce the original deterministic Portal timeout.

Both engines measured seven fully visible 48 px mission rows on a 390×844 screen, for arrivals and departures. The 26-arrival/26-departure Raiden fixture exercised ULD placement, side-by-side desktop boards, read-only access, historical Sort selection, TV paging, both themes and refresh. Operational transitions were exercised only in the disposable DUCC and Sektor fixtures.

Raw release evidence came from `instance/god-browser-final.log` and the existing NeoRaiden visual review archive. The side-by-side replay logs, isolated timeout probe, retest logs, screenshots and viewport measurements are collected in `instance/browser-evidence/legacy-ui-review.zip`. Only local synthetic data was used for browser and API exercises.

The audit modified browser tests and fixture setup. No production source, configuration, operational data or model artifacts changed. The existing Render deployment was checked directly: `dep-dc0bqohsrm7s73c06nt0`, status `live`, SHA `266804a32b0d46417f5c32354911ccf2f9b76923`. The repository's existing `[skip render]` convention permits test repairs to be retained on main without restarting production.
