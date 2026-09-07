# Gateway mobile dock placement

Starting main: `f993c1d24d5ac99ef29e12055c8611551060be90`.

## Findings and correction

Two navigation implementations remained. Operational nodes/Gateway used the
shared `.neo-mobile-bottom`, but auth-owned Gateway utilities rendered the older
`.mobile-bottom-nav`. The latter received the floating-pill rules in
`13-shared.css`: bottom `10px + env(safe-area-inset-bottom)`, translateX(50%),
inset width and 56px minimum height. It did not load the shared dock stylesheet.
The baseline browser audit measured `/motherbrain/permissions` bottom at 834px
in an 844px viewport. `/admin/access-requests` reproduced the same mismatch when
the audit was expanded to the registered Gateway administration aliases.

`base.html` now explicitly marks Gateway shell containers with `data-gateway-dock`
and loads `mobile_drawer.css` for these utilities as well. This does not change
their authorization, header identity, routes or existing popover controller.
The shared stylesheet owns both dock geometries: fixed left/right/bottom zero,
zero margin/translation, 54px controls plus the real safe inset inside the dock.
Content clearance uses that same height plus the existing 20px fade allowance.

Obsolete operational/Gateway pill geometry was removed from `13-shared.css` and
`28-shared.css`; the remaining legacy rule excludes explicitly marked Gateway
containers without increasing its specificity for unrelated pages. Portal and
NeoStaffing are not marked. No desktop layout or header rules were changed.

Legacy switcher popovers were right-aligned to their narrow middle button,
extending 20px outside the viewport. They now anchor to the full dock, with
safe-area-aware width/height bounds. Their existing open/close animation remains.

The audit also found intrinsic input sizing overflowing NeoSubZero Settings.
`23-neosubzero.css` now permits the mobile form children to shrink and constrains
input widths; no global overflow hiding or desktop changes were added.
Affected stylesheet URLs carry the explicit `20260907-viewport-v1` cache token.

## Browser verification

`tests/browser/test_gateway_dock_audit.py` reuses the existing isolated SQLite
fixture, synthetic administrator and sort, and disables external integrations.
It inventories registered GET routes and follows actual same-scope HTML links,
without inventing IDs or issuing POST mutations during the crawl. JSON/export
endpoints and redirects are recorded separately. This covers reachable pages in
the synthetic graph, not every possible production record or UI state.

Commands:

```powershell
instance/security-boundaries/venv/Scripts/python -W ignore -m unittest tests.browser.test_gateway_dock_audit -v
# Additional registered scaffold, checked after the 69-page sweep:
$env:NEO_DOCK_PATHS='/nodes/'
instance/security-boundaries/venv/Scripts/python -W ignore -m unittest tests.browser.test_gateway_dock_audit -v
git diff --check
```

Chromium and WebKit, 390x844, synthetic bottom safe areas 0 and 34px.
Result: 69-page sweep plus the one-page scaffold check passed (70 unique pages,
280 engine/safe-area visits, zero final failures). Every measured dock bottom
was 844px, including open/close states; heights were 54px and 88px respectively.
Assertions cover bottom edge 844px (tolerance 1px), left/right 0/390, height
54/88px, content clearance >=74/108px, no horizontal document overflow,
stationary dock across Menu/Nodes/switch/close, contained drawers/popovers and
unchanged header height during those interactions. Sektor Dashboard remains the
geometry reference. Physical iPhone/PWA and deployed production were not tested.

Local detailed evidence:

- `instance/browser-evidence/gateway-dock-audit/baseline.json`
- `instance/browser-evidence/gateway-dock-audit/gateway-results.json`
- `instance/browser-evidence/gateway-dock-audit/results.json` (additional scaffold)
- Representative Chromium/WebKit screenshots in the same directory.

## Dock-bearing pages audited

Full paths are retained in the JSON inventory. Grouped here by prefix:

- `/rfd`
- `/nodes/`
- `/admin`: `/access-requests`, `/permissions`, `/users`, `/users/all`,
  `/users/edit-users`, `/users/pending`.
- `/motherbrain`: root, `/flight-api-review`, `/flight-api-test`,
  `/gateway-matrix`, `/manage-sort`, `/master-schedule`,
  `/master-schedule/bulk-edit`, `/master-schedule/new`, `/operations`,
  `/operations/new`, `/parking-plan`, `/parking-rules`, `/permissions`,
  `/sort-timeline`, `/system-settings`, `/system-settings/integrations`,
  `/system-settings/node-refresh-timings`.
- Synthetic MotherBrain operation 1: `/motherbrain/operations/1`,
  `/motherbrain/operations/1/alp/arrival`, `/motherbrain/operations/1/alp/departure`,
  `/motherbrain/operations/1/arrivals`, `/motherbrain/operations/1/departures`,
  `/motherbrain/operations/1/missions/new`, `/motherbrain/parking-plan/1`.
- `/neoermac`: root, `/building-lineup`, `/door-view`, `/settings`,
  `/tug-assignments`, `/upcoming-pulls`, `/view-outbound`.
- `/neorain`: `/inbound`, `/load-planner-lineup`, `/outbound`, `/settings`.
- `/neoscorpion`: root, `/fuel-dispatch`, `/fueler`, `/hanzo`, `/history`,
  `/settings`, `/settings/spear`, `/settings/spear/calibration`,
  `/settings/spear/vault`, `/truck-manager`.
- `/neosektor`: root, `/discharge`, `/driver-routing`, `/ebm`, `/live-counts`,
  `/settings`, `/tunnel-conductor`, `/wbm`.
- `/neosubzero`: `/callouts`, `/coordinator`, `/deice-log`, `/deicer-mobile`,
  `/outbound`, `/pretreat`, `/qualifications`, `/settings`, `/ucc` (FROST).

NeoReptile has shell metadata but no registered blueprint/page in current
`register_blueprints`; there is no reachable Reptile dock to measure. Its future
operational shell would use the same shared contract. Non-page Ermac state
endpoints returning 400/428 are inventory entries, not dock failures.
