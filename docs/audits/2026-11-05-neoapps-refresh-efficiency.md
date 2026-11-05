# NeoApps refresh efficiency follow-up — November 5, 2026

## Scope and decision

Starting main and LIVE: `c4138ba972c4cd0cd677f5cd54c3dda24bc413e8`
(`dep-dblv2kajnfac73cp19cg`, live at 03:02:21 UTC).
Implement one confirmed Driver Routing scheduling correction. Retain existing
cross-tab revision sharing, independent authorized state reads and all server
limits. NeoErmac, NeoScorpion, operational algorithms and database code are unchanged.

## Confirmed cause and correction

The Driver version watcher runs every 1.5 seconds alongside the five-second
canonical state controller. It previously called the state fetch directly on a
changed hint. Single-flight protection prevented overlap but allowed a second
request less than five seconds after an accepted request. It also bypassed a
cooldown already held by the controller.

`live_refresh_guard.py` limits registered state requests per authenticated user
and screen, within each worker, with a minimum five-second interval. A rejected
request returns the 77-byte cooling-down response and Retry-After; rejection does
not extend the accepted-request deadline. Driver version hints are outside this
state-request guard. Fresh synthetic requests confirm different users have
separate buckets, while two tabs using the same user share one bucket.

The watcher now requests invalidated state through the existing controller's
`refreshNow()`. Deferred or failed refreshes leave the hint unacknowledged. The
controller retains cadence, visibility, server enablement and Retry-After control;
the watcher retains its existing single-flight and superseded-Sort protections.
The versioned watcher asset URL is advanced. No new timer, polling system, cache,
permission bypass, interval or threshold is introduced.

Existing transport correctly parses numeric/date Retry-After, shares only
session-scoped cooldowns/fingerprints and cancels throttled bodies. No automatic
POST retry is added. Returning tabs retain the existing resume behavior.

## Equivalent isolated measurements

| Workload | Before | After |
|---|---:|---:|
| Real controller, three changes over 15 seconds: state requests | 7 | 4 |
| Same deterministic workload: 429s | 3 | 0 |
| Actual Chromium change: state requests | 3 | 2 |
| Actual Chromium change: 429s | 1 | 0 |
| Chromium generated state-response body bytes | 5,454 | 5,379 |
| Chromium mutation-to-observed-DOM time | 6,668.30 ms | 5,449.48 ms |

The deterministic accepted requests remain exactly five seconds apart. Chromium
used the actual authenticated Driver page, local canonical settings mutation,
real state/version endpoints and production cadence guard, at 390px. Successful
bodies were read; the cancelled 429 body's 77 bytes come from Flask Content-Length.
These are generated body sizes, not encoded network/billed bandwidth. The two
changed payloads differ by two incidental bytes. This is a modest request/error
reduction, not a broad bandwidth-saving percentage or latency guarantee.

A fresh, rejected real endpoint request performs one user-authentication SELECT,
zero writes and zero commits, then stops before canonical context assembly.
Eliminating the redundant request removes that SELECT; accepted-state SQL remains
unchanged. No transaction, connection-pool or database optimization is claimed.

## Production evidence and limits

Both pages of the November 4 06:00–06:10 UTC Driver state logs were independently
re-read: 120 GETs, 97 HTTP 200 and 23 HTTP 429, totaling 177,542 logged body bytes.
The 429 bodies match the guard's 77-byte response. Logs do not identify authenticated
users/sessions; proxy addresses and browser strings cannot establish tab counts.
The reproduced defect explains an avoidable path, not every historical rejection.
The preceding Sektor/Ermac audit records 268 version hints in the same window.

Render's November 4 03:00–November 5 03:00 UTC hourly bandwidth points total
3,538.34 MB and 55,768 recorded HTTP requests service-wide, peaking at 853.57 MB
for the 07:00 UTC hour. These include
all applications and releases. The endpoint-filtered post-release log sample
through approximately 03:14 UTC contains only the preceding release's ten Sektor/
Ermac state-verification GETs and no NeoScorpion requests; no busy operational
population is demonstrated. Full-state/unchanged proportions, user counts, mission
counts and Sort exposure cannot be normalized from these logs. No valid matched
post-release busy period exists for any of the three applications. No production
bandwidth reduction is claimed.

Neon project metadata at approximately 03:16 UTC reports November billing-period
totals: 21,839,658,717 transfer bytes, 348,927 active seconds and 87,645 compute
seconds. These are cumulative project counters, not request/application costs or
before/after rates. Endpoint metadata shows active compute and a 300-second
suspend timeout. One read-only connection snapshot shows two idle connections,
zero idle-in-transaction connections. Neither proves sustained autosuspend behavior.
Hourly branch/project consumption tools are advertised but unavailable from the
connected server (`not returned by tools/list`). No settings/extensions were changed.

## Why further state coordination was not added

Opaque hints already coordinate within the authenticated session/gateway/Sort
partition. Each tab still needs fresh authorization, current-Sort verification,
clock-driven status and canonical state. Sharing operational responses violates
the requested boundary. Serializing all same-user tabs behind a five-second
per-user bucket would reduce some tabs' effective update frequency. Staggering
alone cannot fit multiple independent five-second readers into that bucket.
Retain safe independent reads and shared cooldowns; same-user concurrent sessions
and network timing can still legitimately produce 429s. Do not infer their exact
production contribution or weaken the guard to hide it.

## Focused validation and preservation

* Eight Python IDs passed, plus one Chromium workflow; ten passing subtests are
  separate from unique-ID counts. Fresh authorization, scoped/read-only hints,
  operation-window wake, cadence identity and SQL cost are covered.
* Thirty-two JavaScript tests passed: actual-controller cadence, Retry-After,
  hidden/disabled/resume, single-flight/superseded responses, scope isolation,
  cross-tab hint sharing, leader loss and unsupported-coordination fallback.
* The new JavaScript reproduction failed before the fix (7 versus 4 requests and
  cooldown bypass). The final real browser reproduced the old 429 and verified its
  absence after correction. Initial browser instrumentation was repaired for a
  frozen API, an unrelated controller and intentionally cancelled response bodies;
  no operational guard was weakened.
* PostgreSQL tests were not run: no SQL, persistence, locking or schema behavior
  changes. No broad suite or unrelated browser sweep was run. Existing warnings
  remain visible; tests were not skipped or marked expected-failure.
* Python compilation, JavaScript syntax and `git diff --check` passed before push.
  Existing unrelated files/jobs and synthetic database directories are preserved.
  Production diagnostics were read-only; no operational mutations were performed.

Changed files: `app/static/js/neosektor_routing_watch.js`, the Driver Routing
template, `tests/js/neosektor_routing_cadence.test.js`,
`tests/test_neosektor_routing_cadence.py`,
`tests/browser/test_sektor_routing_cadence.py`, and this report.

Release identity is the main commit containing this report, via existing Render
AutoDeploy. Exact resulting LIVE SHA/deployment, startup, health/login, published
watcher hash, security headers, settings comparison and affected read-only endpoint
checks are recorded after deployment in the completion receipt and final response.
No manual deployment or infrastructure change is authorized or performed.

Local receipts/screenshots: `instance/refresh-efficiency-20261105/`.
Reference: `2026-11-04-neosektor-neoermac-performance.md`.
