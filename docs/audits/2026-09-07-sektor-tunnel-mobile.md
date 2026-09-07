# NeoSektor mobile viewport and Tunnel polish

Starting main: `ea63614f327d4d555934da01e67e938f7fb17c98`.

## Production changes

The Sektor rules in `17-shared.css` imposed `100svh`, `overflow:hidden` and
percentage heights on html/body/shell/content. Dashboard alone was exempt.
Those rules can reserve the smaller viewport in an iOS standalone presentation
even while desktop emulation reports a correct fixed-element bounding box.
The Sektor-only root clamps are removed, not replaced with another dock offset.
Driver shell/content viewport rules in `13-shared.css` are desktop-only now.
Standalone mobile content flows normally; Driver's minimum operational surface
uses the shared header/dock dimensions without sizing the document itself.
`mobile_drawer.css`, its controller and Board View behavior are unchanged.

Tunnel's dedicated mobile stylesheet flattens existing panels, establishes
the approved order and thin separators, centers section headings and keeps
all existing inputs, data hooks and permissions. The approved
`images/neosektor/dashboard_mobile.png` is reused directly as the mobile header
background. No crop or image/font regeneration was performed.

The visible modifier labels are `1ST WAVE BAYS` and `2ND WAVE BAYS`; the
`first_modifier`/`second_modifier` keys remain intact. Driver offset's requested
helper is present. New mobile-only heading/helper elements are hidden on desktop.
The existing refresh status and monitor control remain functional; unlike the
old shared mobile status rule, Tunnel displays the status text in its status row.
The permission-gated Manage Employees action remains available at the end.
Normal vertical scrolling keeps the final offset/action above the fixed dock;
this is not another forced one-viewport console.

NeoFont branding and NeoFontLite subtitle remain separate from the shared
NeoFontPlain workhorse. Static URLs carry `20260907-shared-v1` and
`20260907-flat-v1` tokens, including when Render pins the global static version.

## Focused verification

Existing isolated SQLite/browser fixture; no production data or integrations.

- Chromium and WebKit, 390x844, bottom inset 0 and synthetic 34.
- Dashboard, Live Counts, Tunnel Conductor, EBM, WBM, Driver Routing,
  Discharge, Settings and Manage Employees (DIS/EBM/WBM tabs).
- 44 engine/page/inset combinations: dock bottom 844px; 54/88px total height.
- Root overflow/max-height checks reject the former viewport-clamping contract.
- No horizontal overflow; open/close dock parity; Dashboard's established
  menu-to-dock gap retained.
- Tunnel headings centered, exact labels, all control groups present, input
  rectangles do not overlap, status visible, final offset fully reachable.
- Desktop Tunnel smoke at 1920x1080; desktop styles were not redesigned.
- `/ballmat` redirects to EBM/WBM; `/neosektor/` canonicalizes to Dashboard.
  State JSON and POST endpoints are not additional dock-bearing pages.

Commands (existing local venv Python):

```text
python -W ignore -m unittest tests.test_neosektor_routes -k tunnel -v
python -W ignore -m unittest tests.browser.test_sektor_tunnel_mobile -v
python -W ignore -m unittest tests.test_neosektor_routes.NeoSektorRoutesTest.test_neosektor_mobile_console_leaves_viewport_ownership_to_shared_shell tests.test_neosektor_routes.NeoSektorRoutesTest.test_neosektor_mobile_shell_paints_the_safe_area_dark tests.test_neosektor_routes.NeoSektorRoutesTest.test_neosektor_mobile_viewport_layout_hooks_cover_all_operation_screens -v
git diff --check
```

27 Tunnel tests and 3 focused mobile-shell tests passed. Browser test passed.
Local evidence: `instance/browser-evidence/sektor-tunnel-mobile/`:
`dock-results.json`, Chromium/WebKit `390x844.png` and `390x844-bottom.png`,
and `desktop.png`. Screenshots were visually inspected; initial visual review
caught and corrected inherited named-grid-area overlap between routing/settings
controls. Automated control-overlap coverage now protects it.

Physical iPhone/PWA verification remains outstanding. The identified Sektor
viewport ownership is removed in production CSS, but local emulation does not
certify device behavior. No manual deployment was triggered.
