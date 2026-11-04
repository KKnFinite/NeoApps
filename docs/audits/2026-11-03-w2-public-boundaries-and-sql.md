# W2 — Public request boundaries and attributed SQL efficiency

## Outcome and scope

**D1 is corrected. All 34 assigned original W2 IDs pass.** Public assets no longer resolve authentication/vendor data, terminal obsolete clients stop before that boundary, and measured duplicate vacation-context reads are removed. Private authorization remains enforced. W4's application files and retained-history correction are unchanged.

Fetched and safely synchronized `main`; starting HEAD/origin: **`241b42d3a597bc47642de84210121e7e73ca4de3`**. Render independently reported that SHA LIVE in `dep-dbl7vq7avr4c7387f0mg`. The final release identity follows AutoDeploy verification in the completion response and ignored `instance/w2/live-release.json`; a commit cannot contain its own final SHA.

This closes [the final plan's W2 package](2026-11-03-final-remediation-plan.md), not the full audit. The [per-case evidence register](2026-11-03-w2-evidence.json) identifies every original ID, starting result, actual cause, correction and final result. No test was removed, broadly skipped, marked expected-failure or made green by removing a security check. **The full 367-ID inventory was not rerun.** Its last complete measurement remains 78 passing / 289 failing; package results are not a newly measured global total.

## D1 — Exact public boundary

`vendor_request_boundary()` exempted endpoint `static` but otherwise accessed Flask-Login and queried vendor identity. The existing `_is_public_asset_request()` already knew the complete exact GET/HEAD allowlist, but later request guards used it only after vendor enforcement had run. Consequently, signed-in manifest/service-worker/favicon requests incurred two SELECTs (user plus vendor), failed when the user database was unavailable, and could receive vendor default-deny 403.

The vendor boundary now invokes that **existing exact helper before touching identity**. The endpoint set is unchanged:

`static`, `pwa_manifest`, `pwa_manifest_by_key`, `service_worker`, `apple_touch_icon`, `apple_touch_icon_precomposed`, `favicon_32`, `favicon_16`, `favicon_ico`.

Only GET/HEAD qualifies. A redundant later unconditional `static` exception was removed, so it cannot accidentally exempt unsafe methods after vendor lookup. No URL-prefix exemption is introduced. Private QR, similarly named private paths, operational APIs and unsafe methods retain normal authentication, vendor, session, permission and CSRF guards. Existing liveness handling is unchanged.

The two existing obsolete-client rejection guards are registered before vendor enforcement. An obsolete flight poll or the established stale Google-heartbeat request returns terminal **410 with zero SQL**. Requests using the current protocol continue through all normal guards; tests with a broken loader prove that current requests do not obtain the terminal shortcut. This ordering grants no operational action or data access.

The exhaustive vendor endpoint test was updated to the same exact public GET/HEAD contract. It still enumerates more than 100 private endpoints and rejects non-allowlisted requests; it additionally checks POST/PATCH/DELETE against public endpoint rules. The initial broader run exposed its obsolete `pwa_manifest must be forbidden` expectation; the final security run passes with the stronger method-sensitive assertions.

## SQL attribution, fixtures and measured optimization

### Mandatory security and shell reads remain

The common one-read difference is a real scoped `neoscorpion_vendor_accounts.user_id` SELECT. It is necessary on private requests, including requests by employees who are not vendors. It remains in production. `tests/sql_contracts.py::RequestSql` separates precisely that SELECT from old domain budgets and **asserts exactly one SELECT with a user-ID predicate**. Every other read and every write remains in the original operational budget. A second vendor lookup or a write disguised as that lookup fails. Commit indexes retain their original domain-statement meaning.

The remaining single shell read was traced to `my_alerts.pending_access_request_alert()` and the actual permission key **`neoapps.access_requests.view`**. Staffing rendered pages and Ermac attendance now explicitly assert one such permission lookup, rather than dropping it to satisfy an older budget. Valid bulk POST budgets, dependency-lock CTEs, write counts, stale rollback, request pagination and detail hydration limits remain exact.

Staffing's complete first requests page uses 28 SELECTs and one existing notification INSERT/commit; unchanged requests use 24 SELECTs and no writes. Notifications use 17 SELECTs, including one vendor read, four permission reads, five current request-matching reads, four notification reads, one app-access read, one active-person read and one gateway read. Both six- and eighteen-request populations produce identical counts. The test records the actual permission/vendor attribution and preserves maintenance-service budgets and write/commit assertions. These are current bounded service/security paths, not a claim that any older count difference is automatically acceptable.

### Actual repeated vacation reads removed

The old vacation budgets used uncontrolled `date.today()`. On/after November 1, the deployed carry-forward feature legitimately loads source/target-year definitions and resolves ownership. Comparing that seasonal path to a pre-November budget produced misleading failures.

The two original budget fixtures now supply a deterministic pre-November date and retain their **unchanged** 17- and 9-read ceilings. Independent Oct 31/Nov 1 coverage verifies the seasonal feature, rather than hiding it.

SQL stack attribution also demonstrated that `union_calendars_context()` loaded hierarchy/actor, then `official_calendar_carry_forward_candidates()` loaded those same inputs again inside the same read-only invocation. The latter now accepts the already resolved hierarchy/actor from that caller. Standalone calls still resolve fresh authority. No cross-request/global permission cache is added; mutation authorization is untouched.

At one and five View calendars plus one Official calendar, November context construction improves from **27 to 23 SELECTs**, with identical calendar, ownership, carry-forward and creation-authority projections, zero DML and zero commits. October remains **16 to 16**. A subsequent leadership revocation produces no carry-forward candidates and no create authority on the next fresh call. The same contract passes PostgreSQL. This is a measured four-read reduction, not a Neon CU-hour estimate.

### Other shared causes corrected without operational rewrites

* **Ermac:** three budget methods sent destination text into an interface requiring explicit Master Schedule IDs. Fixtures now link current missions to masters, assign through canonical signed lineup writes, and submit actual IDs. They reach real autosaves/pull aggregation, cold read-only behavior and stale identity protections. No destination-fallback matching is restored. Pull-time assertions use the same explicit identity.
* **Scorpion APU:** the benchmark compared a first cold SPEAR evaluation with a second evaluation having cached prior-plan history. The first correctly performed an additional durable snapshot read/savepoint. Isolated cold caches for both APU states give **53 SELECTs each**, with no writes, while still running complete Dispatch enrichment. APU calculations and production cache behavior are unchanged.
* **Scorpion history:** the retired eleven-read ceiling omitted current visit/provenance, arrival-link, direct-entry and physical-start evidence collections. An exact table map now accounts for **19 reads at both one and thirty assignments**: four current-Sort eligibility reads and fifteen batched history/evidence reads. No per-assignment SELECT is permitted. The read model is unchanged; synthetic legacy completed rows are query fixtures, not evidence that incomplete physical workflows may complete.
* **Schema mock:** a generic connection Mock omitted the deployed continuation-queue migrator's inspector. The fixture now stubs that migrator alongside the existing physical identity migration and asserts both execute twice, retaining advisory lock, lock timeout, table and commit checks. No schema implementation changes. Existing real PostgreSQL additive/bootstrap and concurrency contracts also pass.
* **Sektor:** the one-vendor-read attribution restores existing mutation, conflict and timeline-eager-load comparisons without changing their payload/write/lock assertions. Cold Discharge's old domain ceiling is reduced from 30/31 to the measured 24/25; optimized current-operation reuse uses 23/22 domain reads. Its existing compatibility **PortalAppAccess INSERT plus commit** is retained in the synthetic cold fixture. Warm HTML/state reads remain read-only. Do not mislabel that private cold initialization as public asset work or claim every application GET is write-free.

## Workload measurements

Except where explicitly comparing the new boundary or vacation optimization, these are **current implementation measurements**, not new W2 reductions. Existing tests' legacy-loader/cache-disabled variants confirm previous optimizations still work; those savings are not credited to this release.

| Workload | Population / state | Actual SQL result | Writes / scaling |
| --- | --- | --- | --- |
| Public PWA manifest, worker, icons with cookie | Before → after | **2 → 0 SELECTs** | Zero writes; anonymous, regular, vendor, revoked and disabled-vendor cookie states; GET and HEAD; broken user loader |
| Retired flight poll | Before → after | **1 → 0 SELECTs**, terminal 410 | No mutation, provider or operation work |
| November vacation context | One / five View calendars + Official | **27 → 23** for each population | Zero DML/commit; same projection and current authority |
| MotherBrain changed / unchanged planning | One / fifty missions | **16 / 5 full-request SELECTs** at both sizes | Zero writes/commits; unchanged response omits fragments |
| Ermac attendance | 12 / 112 hydrated authorized people | **22 full-request SELECTs** at both sizes | Zero DML/commits; 21 domain + one vendor read |
| Scorpion history service | One / thirty completed legacy assignments | **19 / 19 SELECTs** | Zero DML; exact table attribution; HTTP/template security overhead is not included |
| Scorpion full Dispatch APU projection | APU off / on, equally cold SPEAR | **53 / 53 SELECTs**, two savepoints/releases each | Zero DML; avoids cold/warm benchmark mismatch |
| Staffing bulk Home updates | Two / one hundred selected employees | **23 / 23 SELECTs + one locking CTE** | Two batched UPDATE statements; only intended affected rows change |
| Staffing bulk employee-status updates | Two / one hundred | **21 / 21 SELECTs + one locking CTE** | One batched UPDATE; stale requests write nothing |
| Staffing request pages | One / 205 synthetic requests | Constant query count for the same actor/filter | Bounded page/detail hydration; no per-item fanout |
| Sektor cold Discharge, optimized | Two / fifty request rows | **24 / 24 full-request SELECTs** with a current operation | Existing one compatibility INSERT/commit; warm requests retain zero writes |

These isolated workloads did not demonstrate a new N+1 requiring a production query rewrite. Constant SELECT count does not mean constant bytes or CPU: visible Ermac people correctly grow from 12 to 112, and the existing bulk graph/revision snapshot hydrates a wider organization than the two changed employees. Narrowing that graph without preserving its authority/revision/lock contract is not justified here. Production savings, end-to-end latency improvement and Neon billing were not measured. Local test timings include instrumentation and are not production latency estimates.

## Validation and remaining results

| Run | Result |
| --- | --- |
| Exact 34 W2 original IDs, baseline | **34 unique IDs failed**; pytest emitted 262 failure events across parameter/subtest paths. Passing containers/subtests do not turn those IDs into passes. |
| Exact 34 W2 original IDs, final | **34 passed**, 268 passing subtests, 34.29 s; no failures/skips |
| Security, CSRF/CSP, vendor, affected reads and W4 SQLite | **127 passed**, 537 passing subtests, 77.11 s |
| Additional affected SQLite parent modules | **130 passed / 1 failed**, 209 passing subtests, 64.93 s; the failure is existing W3 below |
| Disposable PostgreSQL | **33 passed**, 117 passing subtests, 84.55 s; no failures/skips |
| Chromium + WebKit | **One method passed**, 13.34 s; eight real browser identity contexts across both engines |
| Additional MotherBrain/Ermac scaling | **Two passed**, 3.18 s |
| Independent seasonal/revocation benchmark | **One passed**, 1.98 s |
| JavaScript | **24 Node runner tests passed**, no failures/skips; CSRF renewal/drafts, vendor and node timecard autosave |
| Compilation / syntax / diff | Passed; changed Python plus affected JS syntax and `git diff --check` |

Runs overlap; they are not summed into a false unique test count. Existing datetime/ORM warnings remain visible. An intermediate SQLite bulk parent run skipped three explicitly PostgreSQL-only methods; the PostgreSQL run then executed those inherited locking/concurrency methods successfully. No failing test was skipped to close W2.

PostgreSQL coverage uses independently owned schemas in a disposable loopback database: public outage/identity isolation, November read equivalence and fresh revocation, bulk dependency locks and competing writers, vendor/inventory concurrency and idempotent bootstrap, and **nine W4 retained-scope methods**, including transfer/edit serialization. The server was stopped afterward without deleting existing synthetic database files.

The browser test performs genuine regular/vendor logins with canonical synthetic vendor invitation acceptance. For anonymous, regular, vendor and revoked sessions it navigates to all nine public asset paths while the user loader raises a simulated database error. Public requests generate zero SQL. Vendor access to Staffing stays 403, revoked private access returns to login, and both engines record zero page errors. Screenshots are local under `instance/w2/browser/{chromium,webkit}-{anonymous,regular,vendor,revoked}-public.png`. No production vendor account or employee was created for validation.

**Assigned W2 remaining failures: none.** One broader parent failure remains owned by W3:

`tests.test_neoscorpion_apu_truck_projections.ApuTruckProjectionsTest::test_recommended_allowance_changes_only_projected_inventory`

It still expects an identical SPEAR plan token after changing APU projection inputs. This exact ID was already failing and unresolved in the final plan's W3 census. Its assertion and fueling/SPEAR implementation are left unchanged. This package does not decide that operational contract or force it green. No new original failing ID was found in the executed package/parent coverage.

Recommended next package: **W1**, the plan's shared explicit flight/mission identity and live-authorization consolidation. W3 retains the APU/SPEAR question and the other physical-evidence/browser acceptance work. W5 still owns asset metadata/presentation cleanup. Final global sign-off requires the reserved whole-inventory reconciliation.

## Exact changed files and production safety

Application (three files):

* `app/__init__.py`
* `app/services/neoscorpion_vendors.py`
* `app/services/neostaffing_vacation.py`

Shared/new tests:

* `tests/sql_contracts.py`
* `tests/test_public_request_boundaries.py`
* `tests/test_w2_vacation_reads.py`
* `tests/test_w2_read_scaling.py`
* `tests/test_w2_postgres.py`
* `tests/browser/test_public_request_boundaries.py`

Existing tests with focused fixture/attribution corrections:

* `tests/test_db_query_batching.py`
* `tests/test_motherbrain_live_collaboration.py`
* `tests/test_neoermac_read_budgets.py`
* `tests/test_neoscorpion_apu_truck_projections.py`
* `tests/test_neoscorpion_production_schema.py`
* `tests/test_neoscorpion_routes.py`
* `tests/test_neoscorpion_vendor_workflow.py`
* `tests/test_neosektor_conductor_queries.py`
* `tests/test_neosektor_conflict_queries.py`
* `tests/test_neosektor_discharge_queries.py`
* `tests/test_neosektor_integration_modes.py`
* `tests/test_neosektor_mutation_queries.py`
* `tests/test_neosektor_poll_queries.py`
* `tests/test_neostaffing_bulk_change_cost.py`
* `tests/test_neostaffing_change_request_reads.py`
* `tests/test_neostaffing_vacation_selection.py`

Documentation: this report and `docs/audits/2026-11-03-w2-evidence.json`.

No schema change, new infrastructure, background polling, process-global permission cache, production test/repair mutation, historical rewrite, operational-identity change, Google/R2 write or hosting/credential change is included. W4's two application services are untouched. Unrelated artwork, prototype code, audit notes and ignored synthetic database directories remain intact and unstaged.

Release verification uses existing main AutoDeploy only, then exact Render LIVE identity, standard unmodified bootstrap/worker startup, bounded health/login/PWA reads, public asset identity and a bounded error/5xx/OOM/timeout log window. That window is not proof of indefinite future reliability or complete audit closure.
