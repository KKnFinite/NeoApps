# Task 13: scoped revision coalescing and critical Dispatch fragments

Starting SHA: `1c3dd2b75137ca0dcbcb88f6b41ce3a4b9eba581`.
All operational experiments used synthetic isolated records. No production
fueling, deicing, user, inventory, Google or archive test writes were performed.

## Design

The browser has no new periodic scheduler. Existing configured controllers retain
ON/OFF, interval, hidden/inactive pausing and monitor behavior. Scorpion's revision-only
reads use an ephemeral BroadcastChannel scoped by an opaque server HMAC of the
user, authentication session version, CSRF session nonce, role and gateway. The
URL and rendered sort further partition reads. A matching response scope header
prevents an old tab publishing another login's metadata. Only whitelisted primitive
revision fingerprints travel between tabs: never HTML, aircraft/personnel records,
forms, fuel quantities, tokens or actions. Received hints retain their original
request time, expire within the configured interval, and cannot downgrade a known
same-sort revision. Existing DOM generation and optimistic-concurrency fences remain.

Web Locks serialize equivalent revision reads. Fresh-hint requests handle a newly
opened tab. Lock waits are bounded at two seconds; revision requests time out at
ten seconds. Closing/hiding the owner does not leave a permanent leader. Missing,
disabled or failed APIs fall back to independent polling. Page teardown closes the
channel; pages without a live channel continue independent reads. All actual
mutations still use the existing server authorization, plan tokens and revisions.
Messages never trigger a mutation. No mutation is replayed.

Read-only helpers respect numeric and HTTP-date Retry-After. Endpoint cooldowns
ignore changing revision query parameters. Cooperating tabs share only the deadline.
Shared controllers display cooling/reconnecting status and automatically resume.
NeoSektor, NeoErmac, NeoRain and SubZero full-state polls use this read-side backoff;
their operational payloads are not broadcast. Authentication/registration limits,
server thresholds and external Google/provider retry policies are unchanged.

Critical Dispatch requests opt into delta=1. They render the original mission groups
through a shared trusted partial, compare request-local SHA-256 fingerprints and
send changed groups/assets only. The client reuses the existing draft-safe patcher.
The request header carries at most 80 mission hashes plus the assets hash, within
a 6,500-character parsing bound. Extra missions are always sent, never omitted.
No server-side operational cache or alternate state model was added. Wrong-sort
requests fail with 409; older generation responses cannot replace newer state.
Checksums normalize CSRF values only for comparison, never rewrite delivered forms.
The first critical response establishes its fragment baseline. Full initial pages,
full refreshes and the legacy completion_only path remain complete and compatible.

## Measured evidence (isolated, not production savings)

Six deterministic polling opportunities: revision GETs were 6 -> 6 for one tab,
12 -> 6 for two tabs and 30 -> 6 for five tabs. An unchanged revision caused no
unnecessary full-panel GET. In the simulated five-tab throttle, only one request
reached the mock revision server before the shared 12-second cooldown; the next successful
attempt coalesced to one read. Unsupported coordination retains independent reads.

Real Chromium, two authenticated tabs, 16.2 seconds: 6 -> 3 revision GETs,
624 -> 312 response-body bytes and 7,866 -> 4,530 browser-reported HTTP bytes.
WebKit measured 3 -> 3 GETs, 312 -> 312 body bytes, 3,924 -> 3,933 HTTP bytes.
No reduction is claimed for that WebKit window. Native cross-tab hint sharing and
continued polling after an owner closes passed on both engines. These are small
correctness/performance fixtures, not a simultaneous-user capacity guarantee.

36-mission fixture, uncompressed JSON response bodies:

| Critical follow-up state | Legacy broad response | Delta response |
|---|---:|---:|
| Unchanged | 555,911 B | 3,018 B |
| One mission newly submitted/eligible | 559,668 B | 41,110 B |
| Unchanged after submission | 559,668 B | 3,018 B |
| Total of these three reads | 1,675,247 B | 47,146 B |

The full required response remained 565,358 B; first delta baseline was 547,999 B.
Delta delivery is not a claim that the initial/full board is small. The twelve-mission
starting implementation measured 209,101 B full and 204,886 B critical; the refactored
full response measured 209,389 B, preserving its information (minor whitespace growth).
Critical context work remains: 71 SELECTs for an unchanged twelve/36-mission snapshot,
72 for the newly submitted example. No critical INSERT/UPDATE/DELETE was introduced.
Single warm twelve-mission observations were 25.7 ms old versus about 27.9 ms new;
this is not evidence of reduced per-request SQL or a statistically proven CPU gain.
The gain is fewer redundant revision requests and substantially smaller follow-ups.

Canonical submission -> enabled COMPLETE, with a different dirty row preserved and
zero full-panel GETs: Chromium 5.009 -> 4.962 s; WebKit 4.951 -> 4.937 s, at a five-second
configured cadence. No claim of significant latency improvement is made.

A bounded production log sample since Oct 25 contained 47 actual HTTP 429 GETs:
24 Sektor routing, 4 Sektor ballmat, 15 Ermac Door View, 4 Dispatch revision. Three
other matches were not HTTP 429 responses and were excluded. The query was paginated;
this does not reproduce or classify the entire previously reported seven-day count.
No conclusion about absence of authentication, registration or provider throttling
outside that sample is justified. No production bandwidth/CU savings were measured.

## Validation and known baseline failures

99 focused Python tests passed; 13 disposable PostgreSQL tests passed, including
concurrent source submission/verification; 111 JavaScript tests passed. Nine unique
browser workflows passed across Chromium/WebKit, including Dispatch/Fueler drafts,
completion and scrolling, SubZero Operations, NeoRain delay drafts, Ermac mission
identity, cross-tab behavior and measured eligibility latency. Compilation, UTF-8,
JavaScript syntax and diff checks passed. Local PostgreSQL was stopped afterward.

The legacy `test_neoermac_live_integrity`, old static assertions in
`test_live_screen_refresh` and two assertions in `test_neoscorpion_live_refresh`
remain stale against existing canonical mission identity and previously moved client
implementations. Starting-SHA templates/source assertions replayed in memory produced
16 failing assertions/subtests and one IndexError across 31 tests. Ermac server code
was not changed by Task 13. The current flight-identity Python/browser suite passed.
The SPEAR source-location assertion was updated to follow the extracted owned partial;
its content checks remain. Existing SQLite cleanup/deprecation warnings remain.

The receipt/fencing, background deadline, FROST/model, employee recovery, authority,
permission, fuel/deice and hosting configuration implementations were not changed.

## Exact changed files

- `app/__init__.py`
- `app/neonodes/neoscorpion/routes.py`
- `app/services/live_screen_refresh.py`
- `app/services/neoscorpion_dispatch_fragments.py`
- `app/static/js/live_revision.js`
- `app/static/js/live_updates.js`
- `app/static/js/neoermac_door_live.js`
- `app/static/js/neoermac_lineup_live.js`
- `app/static/js/neoermac_outbound_live.js`
- `app/static/js/neoermac_upcoming_pulls.js`
- `app/static/js/neoscorpion_fuel_assignments_live.js`
- `app/static/js/neoscorpion_fuel_dispatch_live.js`
- `app/static/js/neoscorpion_fueling_board_live.js`
- `app/static/js/neoscorpion_hanzo_live.js`
- `app/static/js/neosektor_live.js`
- `app/static/js/neosubzero_departure.js`
- `app/static/js/neosubzero_operations.js`
- `app/static/js/neosubzero_ucc.js`
- `app/templates/base.html`
- `app/templates/neonodes/neoermac/building_lineup.html`
- `app/templates/neonodes/neoermac/door_view.html`
- `app/templates/neonodes/neoermac/upcoming_pulls.html`
- `app/templates/neonodes/neoermac/view_outbound.html`
- `app/templates/neonodes/neorain/inbound.html`
- `app/templates/neonodes/neorain/outbound.html`
- `app/templates/neonodes/neoscorpion/_dispatch_critical.html`
- `app/templates/neonodes/neoscorpion/_dispatch_mission_rows.html`
- `app/templates/neonodes/neoscorpion/_fuel_dispatch_panel.html`
- `app/templates/neonodes/neoscorpion/fuel_dispatch.html`
- `app/templates/neonodes/neoscorpion/fueler.html`
- `app/templates/neonodes/neoscorpion/fueling_board.html`
- `app/templates/neonodes/neoscorpion/hanzo.html`
- `app/templates/neonodes/neoscorpion/truck_driver.html`
- `app/templates/neonodes/neosektor/ballmat.html`
- `app/templates/neonodes/neosektor/discharge.html`
- `app/templates/neonodes/neosektor/driver_routing.html`
- `app/templates/neonodes/neosektor/live_counts.html`
- `app/templates/neonodes/neosektor/tunnel_conductor.html`
- `app/templates/neonodes/neosubzero/coordinator.html`
- `app/templates/neonodes/neosubzero/deice_log.html`
- `app/templates/neonodes/neosubzero/deicer_mobile.html`
- `app/templates/neonodes/neosubzero/operations.html`
- `app/templates/neonodes/neosubzero/outbound.html`
- `app/templates/neonodes/neosubzero/ucc.html`
- `docs/audits/2026-11-01-multitab-refresh.md`
- `tests/browser/test_dispatch_refresh_latency.py`
- `tests/browser/test_multitab_revision.py`
- `tests/js/live_revision.test.js`
- `tests/js/neoscorpion_dispatch_critical_completion.test.js`
- `tests/test_dispatch_refresh_fragments.py`
- `tests/test_dispatch_refresh_postgres.py`
- `tests/test_neoscorpion_spear_decisions.py`
