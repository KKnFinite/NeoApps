# Deice Operations: desktop always shows all ramps

Starting main/origin/main: `06848235ccee90fa97da7fd07c3e9ef7a16bafb3`.

The starting Chromium reproduction saved Bravo through the canonical per-user preference service. Desktop PRETREAT Dispatch List then displayed **1 of 9 applicable aircraft**. The visual layout had the same defect: it created all columns but hid aircraft using the saved single-ramp preference.

## Correction

- `neosubzero_operations.js` now derives an effective display ramp of **All** above the existing 900px mobile breakpoint. Both list filtering and visual-row filtering use that effective value. Server ETA/ETD ordering and existing cleared-aircraft ordering are preserved.
- The RAMP control's label is mobile-only, including removal from desktop layout and accessibility navigation. The shared control remains available when the same page changes to a mobile width.
- The saved `data-ramp`/database preference is retained. Desktop layout changes continue sending layout-only preference patches. Returning to mobile restores the saved ramp; mobile's existing All/list and single-ramp visual behavior remains intact.
- Existing media-query and live-refresh callbacks reapply this display rule. No new request, timer, endpoint, permission, schema, preference reset or operational mutation is introduced.
- Operations CSS/JavaScript URLs are versioned `20261103-desktop-all-ramps-v1`.

Changed application files are limited to `app/static/js/neosubzero_operations.js`, `app/static/css/neosubzero_operations.css`, and `app/templates/neonodes/neosubzero/operations.html`. Preference services/models, treatment handlers, aircraft identities, DUCC and FROST are unchanged.

## Validation

- Focused Operations, navigation and Coordinator Python tests: **26 passed, 7 passing subtests**.
- Existing SubZero JavaScript tests: **5 passed**. JavaScript syntax, Python test compilation and diff checks passed.
- New Chromium/WebKit responsive browser tests: **2 passed**, exercising PRETREAT/OUTBOUND × list/visual at 320, 375, 390, 430, 900, 901, 1366 and 1920px. All 64 measurements have no horizontal page overflow. Desktop shows all nine fixture aircraft and all five primary ramp columns plus Remote/Location Review where applicable. Mobile retains Bravo and displays its aircraft. Direct database checks confirm the saved ramp is still Bravo after every viewport change and desktop layout save.
- The new cases also verify canonical list order, automatic visible ETD updates, unsaved Application Context notes, and restoration of mobile selection after a desktop live refresh.
- The existing complete Operations browser workflow passed as an exact class target (**1 test, both engines**), avoiding imported suites. Its fixture now sets ramp preferences through the actual mobile selector before returning to desktop. Treatment, version/permission, draft, tail-identity and mobile workflow assertions remain intact.

All mutations use isolated synthetic SQLite/browser fixtures and blocked external providers. No production treatment, aircraft, staffing, preference or archive test writes are performed. No full-system green-suite claim is made. Existing datetime deprecation warnings remain visible.

Synthetic evidence: [desktop list](assets/subzero-desktop-all-ramps/chromium-outbound-list-desktop.png), [desktop visual board](assets/subzero-desktop-all-ramps/chromium-outbound-visual-desktop.png), [mobile saved Bravo](assets/subzero-desktop-all-ramps/webkit-outbound-visual-mobile.png), [Chromium measurements](assets/subzero-desktop-all-ramps/chromium-measurements.json), [WebKit measurements](assets/subzero-desktop-all-ramps/webkit-measurements.json).

Release follows the existing main push → Render AutoDeploy path. The completion response records the exact final SHA, deployment ID and read-only production checks. Unrelated untracked files, artwork, operational records and preserved synthetic PostgreSQL directories are excluded from this change.
