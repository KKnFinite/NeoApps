# EBM/WBM spotter modes — 2026-09-07

## State and transactions

The existing `neosektor_ballmat_counts` row is already unique by sort and side.
Four additive columns extend that row: `spotter_mode` (default 1),
`right_first`, `right_second`, and `right_open` (default 0).
The existing wave-count and open-bay rows remain the published totals. Left is
derived as total minus right. There is no second aggregate source of truth.

Changing 1→2 allocates the existing total to left, with right zero. Changing
2→1 clears the split without changing the total. EBM/WBM modes are independent.
Individual +/- commands contain the counter, position, delta and expected mode;
they never submit the other operator's stale panel snapshot. An outdated mode
returns 409 and the UI reconciles without retrying the write.

Normal NeoSektor write bundles lock the Gateway row before loading sort state.
PostgreSQL uses `SELECT ... FOR UPDATE`; isolated/local SQLite uses an equivalent
write reservation. This also serializes initial sort creation among writes.
The lock spans the existing transaction, including split, total and rollups;
opposite-device edits read the preceding committed state. There is no process
mutex, background worker, new polling loop, or automatic write retry.

The existing aggregate limit is still 99. Existing absolute aggregate edits
(Tunnel/desktop) adjust the derived left allocation, clamping right if the new
total is smaller. Spotter detail is returned only in the operator response for
the selected permitted Ballmat. Shared consumers continue to receive aggregates.

In Neo-primary + Google-mirror mode, the existing database-first commit and
best-effort changed-cell mirror remain intact. Google receives B2/B3/B4 or
C2/C3/C4 totals, never left/right allocations. Mode changes alone do not produce
count mirror writes. No integration setting or authority mode is changed.
The retained legacy Google-primary mode continues its aggregate-only workflow;
two-spotter controls require Neo-owned counts.

## Schema deployment

Both SQLite and PostgreSQL additive schema-sync maps include the four columns.
The canonical `python scripts/bootstrap_database.py` deployment/pre-deploy path
must complete before this worker version serves requests against an older
database. No schema work was added to web startup, routes, or polling. Existing
rows retain their totals and default to one spotter. No production database or
Render setting was accessed for this change.

## Mobile presentation

Only the operator page loads `neosektor_ballmat_mobile.css` and its controller.
The existing dashboard mobile artwork is reused unchanged. Legacy desktop DOM
and presentation remain; mobile uses flat wave, Ballmat, bay and routing sections.
The shared fixed header/dock remain owners of their geometry. Page content adds
the 16px difference for the 88px artwork header; no body/shell viewport lock or
per-page dock positioning was introduced. Shorter exceptional viewports can
scroll rather than clipping controls. Normal 390×844 composition fits at rest.

NeoFont branding and NeoFontLite identity remain; workhorse text inherits the
shared NeoFontPlain. The existing live controller handles monitor mode and polls.
Routing labels map the existing canonical routing display state, not a new
client-side routing calculation.

## Focused verification

- `python -m unittest tests.test_neosektor_spotters -q`: modes, transitions,
  totals, existing aggregate edits/limits, independent-connection concurrent
  edits, selected-side permissions, Google mirror behavior, additive schema and
  revision changes.
- Selected existing NeoSektor route/integration tests protect read-only access,
  Tunnel/shared aggregate propagation, no-change polling and legacy query budgets.
- `python -m unittest tests.browser.test_ballmat_spotters_mobile -q`: Chromium,
  EBM/WBM × 1/2 at 390×844, actual login/CSRF requests, controls, fonts,
  zero document overflow, content clearance, stationary drawer/dock; synthetic
  44px top/34px bottom safe areas and one 1920×1080 desktop smoke check.
- Evidence: `instance/browser-evidence/ballmat-spotters/` (four mobile captures,
  desktop capture, `geometry.json`). No physical-iPhone/PWA or production claim.

Final run: 20 focused backend/route/integration tests passed (18.413s); the
four-mode Chromium browser test passed (17.913s). All four documents measured
390×844 without overflow; every dock bottom was 844px and the operator content
ended at 759px, above the dock top at 790px. The synthetic-safe-area check passed.
Touched-Python compileall, JavaScript syntax check and `git diff --check` passed.

Changed files:

- `app/models/neosektor_ballmat_count.py`
- `app/neonodes/neosektor/routes.py`
- `app/services/neosektor_live_counts.py`
- `app/services/neosektor_live_refresh.py`
- `app/services/schema_sync.py`
- `app/templates/base.html`
- `app/templates/neonodes/neosektor/ballmat.html`
- `app/templates/neonodes/neosektor/_ballmat_mobile.html`
- `app/static/css/neosektor_ballmat_mobile.css`
- `app/static/js/neosektor_ballmat_mobile.js`
- `tests/test_neosektor_spotters.py`
- `tests/test_neosektor_routes.py`
- `tests/browser/test_ballmat_spotters_mobile.py`
- this document
