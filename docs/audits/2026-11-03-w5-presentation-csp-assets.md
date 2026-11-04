# W5 — Presentation, CSP and asset integrity

Date: 2026-11-03 (America/Chicago). Scope: the 49 W5 original IDs assigned by the final remediation plan. Starting and fetched `origin/main`: `680556746e55f11f9b2929e03f25ba4b7cf1bec0`. The tracked checkout was clean and was safely fast-forwarded before editing. W1–W4 work is retained.

## Outcome

All **49 original W5 IDs pass**, with 89 passing subtests and no skips, in one consolidated invocation (35.22 seconds). The unchanged starting release reproduced all 49 failures (72 failure events and 50 passing subtests). Failure events are not unique IDs. The machine-readable companion `2026-11-03-w5-evidence.json` preserves every original ID, original failure, classification, verified replacement contract and final result.

**D2 is the only confirmed application defect corrected here.** Only `app/static/css/sequence.json` changes under application source. There are no stylesheet, script, template, permission, API, schema, source-authority or operational behavior changes. Existing UI is verified rather than redesigned.

The full 367-ID inventory was deliberately **not** run. This package makes no new full-inventory or full-system-green claim.

## D2: exact metadata correction

`25-neosubzero.css` was already the approved palette source; its manifest still described an older revision:

| Field | Previous declaration | Exact committed LF source |
|---|---|---|
| bytes | 19,118 | 18,771 |
| SHA256 | `44b52d0d88d9d432f45c19f79aa570ae2091d2a889345d3034187033b9f92b88` | `1927a88a9d48124610eac4ef9382e96be3102a61c023a18e40ed3519b382b6fc` |

All **29** manifest fragments were independently checked against `git show HEAD:app/static/css/<file>` bytes. CSS25 was the only mismatch. Their order, scope and CSS contents remain unchanged. Historical `original_sha256_lf` (`d1c8f8e86b01b78e582445781cfa7322f9f92627e0067caec8358c38a24045cb`), `original_bytes_lf` (983,978), and description are preserved as historical provenance, not recalculated as a current concatenation.

The manifest test still validates every fragment. New delivery coverage requests all 29 files, checks size/hash/content type and ETags, and verifies conditional requests return 304 with no body. Windows checkout CRLF is normalized only for this manifest's explicitly LF source identity. Production verification compares actual served bytes directly with Git blobs. The existing CSS25 palette cache key remains `ucc=20261103-palette-v1`: CSS contents did not change. Common asset invalidation is checked by rendering with changed `STATIC_ASSET_VERSION`, rather than freezing old token literals. CSP-owned external assets and original cascade selection remain enforced.

## Consolidated contract repairs

* **Shared shells and navigation:** semantic DOM checks retain actual icons, account controls, POST logout, one inert/hidden drawer, current screen links, desktop/mobile shell scopes and board controls. Account details intentionally live outside node screen drawers. MotherBrain's current mobile menu does not duplicate Manage Sort; its authorized route remains tested independently. Staffing uses its full screen catalog plus a distinct ready-screen launcher. Recovery is Master/People-edit controlled; ordinary approved read permissions are not the retired role-threshold model.
* **MotherBrain parking:** parse the trusted JSON bootstrap by DOM identity/type, preserving its explicit nonce, instead of matching a script tag without attributes. Keep reusable slot editor, tail choices, occupancy and collection-reuse assertions. Two SQL cases separately attribute the mandatory vendor-authority SELECT while retaining the original 15/18 domain-query ceilings, commit counts and operational-table assertions. No permission query was removed or budget blindly increased.
* **Rain:** inspect actual server-rendered 21-column desktop and 7/8-column mobile boards, current full-width status rows, actual override controls, authority/version metadata, distinct eLMAC/fuel columns, late-inclusion controls and reset. Approved ETD/eLMAC overrides are not incorrectly banned by obsolete three-editor assertions. Cancelled rows retain readouts and disabled controls; a forged cancelled override is rejected without a version or timestamp change. No Return is entered through the real authorized action before checking its confirmation-required reversal. A real CSRF-protected logout/login verifies the view-only account lacks creation links. Current Rain mission forms delegate to canonical MotherBrain services and require the current operation, rather than an old MotherBrain URL string.
* **Rain reporting:** briefing output is compared exactly to the dedicated daily-briefing context/formatter actually used by the route, not the superseded report formatter. Load-planner and board CSS assertions inspect the referenced owned stylesheets after CSP extraction.
* **Staffing/Sektor:** use actual app approval, time segments and current roster controls. Numeric HHMM fields retain original timestamp/version identity; roster scrolling is bounded and keyboard accessible. Current navigation ordering includes retained records, timecards, accountability, vacation and authorized recovery. TV and regular discharge notices retain distinct formatting; orientation-scoped TV rules are preserved.
* **Fonts and scripts:** keep glyph/cmap/vector checks; verify fonts are referenced/preloaded only by their current typography scopes. Parse every executable script on the real People response with Node, require explicit nonces for trusted inline scripts, owned same-origin external paths and no inline event handlers. An eligible shared Google heartbeat is rendered under its preflight boundary with one nonce, CSRF access and no operation/sort payload. Existing security suites independently exercise the real boundary and token renewal.
* **Time fixtures:** flight review uses coherent UTC/local schedule timestamps, deterministic current-sort clocks and summer/winter cases beyond the existing matching window. It retains exact schedule/API time, diagnostic difference and reason assertions.

Node.js is required for the rendered-JavaScript syntax case, as well as the existing JavaScript suite. No test was deleted, skipped, marked expected-failure, broadly relaxed, or made green by changing operational source. An AST comparison checks that existing test-method edits are confined to the 49 IDs, plus the browser acceptance helpers/cases described below. The added cache-delivery case is additional acceptance coverage.

## Browser acceptance

The Rain helper now opens the existing mobile detail disclosure before interacting with controls that are intentionally hidden there. It does not expose unauthorized controls or change the UI. This repairs the shared setup for real inline saves/reset, null clears, incoming Google observations, active overrides, queued responses and dirty partial/blank drafts. All six Rain browser methods pass in the combined run's completed Rain section; final browser totals are recorded below.

The broad browser command collected 30 methods spanning Rain, shared shells, fonts, Staffing editor, public assets and enforced-CSP operational workflows. It reported **29 passing results and 3 failure events** (794.69 seconds): two unique methods, with the font failure appearing once per engine. There were no errors or skips. All eight enforced-CSP methods passed, including blocked untrusted scripts/event handlers, authenticated node assets/navigation, aged native/AJAX/dynamic CSRF forms, real Fueler/Dispatch/Staffing/Rain/Ermac/SubZero/Sektor actions and live refresh.

Both additional failures were independently traced to obsolete browser assertions and corrected without application changes:

* `test_mobile_vector_font`: the old seven-label list omitted current Stats/Employees entries and had old ordering. Assert the exact current nine screen identities, nonempty rendered labels, actual loaded font and bounded glyph geometry. Both Chromium and WebKit pass.
* `test_portal_whole_card_actions`: a synthetic denied Staffing grant was incorrectly expected to become pending from an ordinary request. `access_control._request_reinstatement()` and the N01 state-matrix regression explicitly preserve denial. The browser now verifies that denied access remains denied, its protected page rejects access, and a separate controlled inactive prior approval can request pending review while remaining unauthorized. Whole-card click, CSRF rejection, pending noninteractive markup and final database state assertions remain exercised.

The affected-method rerun reports **2 passed**, zero errors/skips, 27.83 seconds (`browser-contracts.xml`). The broad initial failures remain in `browser.xml`; there was no second clean 30-method combined run. All browser methods have passing evidence after their applicable corrections, rather than a claim that the first broad command was green. A separate login-hero diagnostic passed unchanged (22.46 seconds); login was not the broad run's failing method.

Shared-shell acceptance covers 320px and 390px phones and desktop collapse/layout. Rain adds 375px, 390px, 1440px and 1920px board/detail geometry. Enforced-CSP workflows exercise both engines, real permissions and client actions, renewal and dirty drafts. The browser runs use isolated SQLite records and mocked external providers; PostgreSQL authority/rollback behavior is independently covered in the table below.

Evidence screenshots remain local under `instance/browser-evidence/mobile-drawer/`, including `rain-inbound-inline-{chromium,webkit}-{1920,390,375}.png`, outbound equivalents, `rain-clear-*-webkit-390.png`, and shared shell desktop/mobile images. No employee or aircraft production mutations were used for browser evidence.

## Measured validation

Counts below are separate, potentially overlapping command scopes; they must not be added as a unique global total.

| Scope | Measured result | Evidence |
|---|---|---|
| Original W5 batch | 49 passed, 89 passing subtests, zero skips, 35.22 s | `instance/w5/release.xml` / `.log` |
| Complete selected presentation modules | 56 passed, zero skips, 27.10 s | `presentation-modules.xml` |
| Full asset delivery/cache module | 4 passed; all 29 files and 304 checks; 2.84 s | `asset-delivery.xml` |
| W1/W2/W4 authority, revocation, public boundaries, CSP/CSRF, Rain override and load precedence | 97 passed, zero skips, 47.29 s | `security.xml` |
| Disposable PostgreSQL | 18 passed, zero skips, 73.03 s | `postgres-verified.xml`; W2 public/session and seasonal authority, W4 retained-scope/transfer/rollback, W3 resource-PROF/concurrent completion |
| Browser | Broad initial 29 passing results / 3 failure events; both affected methods subsequently pass (2/2) | `browser.xml`, `browser-contracts.xml`; no skips |
| Curated NeoScorpion core | 300 passed, zero skips, 238.00 s | `core.json` / `.log` |
| All tracked JavaScript tests | 402 passed, zero skipped/cancelled, 1.10 s | `js.log`; unrelated untracked prototype not collected |
| Compilation / syntax / diff | Changed Python compiled; rendered People scripts and tracked JS parsed; `git diff --check` passed | `static-checks.json`, script-parsing case and Node tests |

An initial PostgreSQL invocation selected `postgresql+psycopg`, which is not installed in this environment: 18 setup failures, no test bodies executed. The corrected URL uses the repository's installed `psycopg2` driver and all 18 pass. No dependency/configuration change was made. Existing deprecation warnings are retained, not suppressed.

## Release, preservation and limitations

Release is through a focused main commit and existing Render autoDeploy only. Pre-release Render was LIVE at the exact starting SHA, deployment `dep-dblaf9tq3nlc73dqss40`. Exact final SHA/deployment, post-release health, CSP/HSTS, authorized views, served Git asset matches and rollout-log checks are recorded in the final delivery response and `instance/w5/live-http.json` / deployment receipt after the commit exists.

All 12 underlying files in the six unrelated untracked paths were hash-checked and preserved. Existing jobs, sessions, histories, unfinished work, ignored synthetic database directories and artwork were not deleted, cancelled, archived or reset. A new loopback-only synthetic PostgreSQL fixture uses `instance/w5/pg`; no production database is used. No production records, Google Sheets, R2 archives, credentials, permissions, FROST data or hosting configuration are changed.

The next task is the **single full 367-ID reconciliation** after W1–W5, plus current authority/operational/security suites and explicit treatment of any additional browser limitations. No new aggregate pass count is inferred from W5's 49 passing IDs.

## Exact changed files

* `app/static/css/sequence.json`
* `tests/browser/test_mobile_drawer.py`
* `tests/browser/test_neofontlite.py`
* `tests/browser/test_neorain_compact_overrides.py`
* `tests/test_flight_api_import.py`
* `tests/test_frost_phase10.py`
* `tests/test_local_launch_navigation.py`
* `tests/test_motherbrain_routes.py`
* `tests/test_neofontlite.py`
* `tests/test_neofontplain.py`
* `tests/test_neorain_board_layout.py`
* `tests/test_neorain_load_planner_routes.py`
* `tests/test_neorain_outbound.py`
* `tests/test_neorain_ui_corrections.py`
* `tests/test_neosektor_css_cleanup.py`
* `tests/test_neosektor_discharge_controls.py`
* `tests/test_neostaffing_routes.py`
* `tests/test_neostaffing_shell.py`
* `tests/test_operational_shell.py`
* `tests/test_page_assets.py`
* `tests/test_shared_ux_hooks.py`
* `tests/test_shell_metadata.py`
* `tests/test_staffing_live_ux.py`
* `tests/presentation_contracts.py`
* `tests/neorain_presentation_fixture.py`
* `docs/audits/2026-11-03-w5-presentation-csp-assets.md`
* `docs/audits/2026-11-03-w5-evidence.json`
