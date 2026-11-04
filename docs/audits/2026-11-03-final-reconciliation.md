# Final audit reconciliation and sign-off — 2026-11-03

## Decision

**CONDITIONAL PASS — acceptable for continued operation within the verified scope; not an unconditional full-system certification.**

The complete original 367-ID inventory now passes in one fresh invocation: **367 passed, 0 failed, 0 skipped, 0 missing**, with 702 passing subtests. All five packages and the previously repaired cases remain intact together. The three confirmed application defects D1–D3 are corrected. No new confirmed Critical or High application defect was established by this assessment.

Additional tests outside that inventory still expose coverage and harness problems. The broader suite is not green, the full PostgreSQL matrix is not a clean certification, and sustained Render memory stability and actual Neon CU savings remain unproved. These limitations are conditions on sign-off, not silently waived findings. No remediation, source change or test repair is initiated by this report.

## Identity, scope and preservation

Fetched `origin/main` and safely synchronized main. Exact audited application/test SHA and independently confirmed starting Render LIVE SHA: **`7a66ab58609c746022ab50d8ab02d67ee56ebefc`**, deployment **`dep-dblb5297lnhs739h9nag`**, LIVE since 2026-11-04 04:22:11 UTC. The audit date is November 3 in America/Chicago; evidence timestamps use UTC and extend into November 4. No applicable repository/ancestor AGENTS.md was found.

Inputs include the [finite remediation plan](2026-11-03-final-remediation-plan.md), original R03/R08 and R03J registers, W1–W5 completion reports and machine-readable evidence, and current source. Previous summaries were not used as substitutes for execution. W1–W5 commits were inspected for their actual scope.

Only this report and its [evidence register](2026-11-03-final-reconciliation-evidence.json) are published. Application code and regression tests are unchanged. Before publication, SHA256 preservation checks compare all 2,494 pre-existing tracked files and the 12 files in six unrelated untracked artwork/prototype/documentation paths. Existing jobs, sessions, histories, unfinished work and synthetic database directories were not deleted, cancelled, archived, reset or cleaned up. New audit outputs live separately under ignored `instance/final-signoff-20261103`.

All synthetic mutations used isolated SQLite or loopback PostgreSQL, mocked providers and the existing network guard blocking non-loopback socket connections. Production inspection was read-only: HTTP GETs, authenticated navigation without action submissions, Render metrics/logs/metadata, Neon metadata and one bounded FROST-receipt SELECT. No production fueling, employee/timecard, inventory, permission, Google, R2, schema, credential or hosting-setting test mutation or repair was issued. Existing application background processing was left enabled; production is an active system, not a byte-frozen synthetic database.

## Original inventory: exact reconciliation

The 367 original IDs were checked for set equality with `2026-11-02-r03-r08-regression-evidence.json`, then mapped through R03J. Every original has one unique collected current ID; no duplicate, skipped, missing or unexecuted ID is counted as passed. The companion JSON lists every original ID, current ID, node ID, owner and measured outcome.

| Owner | Passed | Failed | Skipped | Missing |
|---|---:|---:|---:|---:|
| Previously repaired R03A–J | 78 | 0 | 0 | 0 |
| W1 — identity and authorization | 141 | 0 | 0 | 0 |
| W2 — public boundaries and SQL | 34 | 0 | 0 | 0 |
| W3 — fueling evidence/resources/reporting | 41 | 0 | 0 | 0 |
| W4 — retained staffing/authority | 24 | 0 | 0 | 0 |
| W5 — presentation/CSP/assets | 49 | 0 | 0 | 0 |
| **Total** | **367** | **0** | **0** | **0** |

The one documented rename is preserved, not counted as a missing test or a new pass:

* Original: `tests.test_google_rain_live_milestones.GoogleRainLiveMilestonesTest::test_native_and_unattributed_milestones_are_protected`
* Current: `tests.test_google_rain_live_milestones.GoogleRainLiveMilestonesTest::test_native_block_out_and_unattributed_load_projections_have_distinct_authority`

The fresh invocation took **288.93 seconds** of pytest execution. Its 702 passing subtests are additional assertion contexts, not another 702 original case IDs. The prior measured 78/289 baseline is superseded by this measured 367/0 result; no subtraction from package summaries was used.

## D1, D2 and D3 independently reverified

| Defect | Current evidence | Outcome |
|---|---|---|
| **D1: public PWA assets depended on user/vendor database state** | `neoscorpion_vendors.vendor_request_boundary()` applies the existing exact public GET/HEAD predicate before user/vendor loading. Current public-boundary cases cover anonymous, ordinary, vendor and revoked sessions, unavailable user loading and zero authentication/vendor SQL. Unsafe methods, private pages/APIs, QR and stale clients retain rejection. Public manifest, service worker and favicon also return 200 in production. | **Verified corrected.** No allowlist broadening or permission removal. |
| **D2: stale SubZero CSS metadata** | All 29 manifest entries match Git/LF source and all served fragment bytes. CSS25 is exactly **18,771 bytes**, SHA256 **`1927a88a9d48124610eac4ef9382e96be3102a61c023a18e40ed3519b382b6fc`**. Current asset/cache cases remain in the 367 passing batch. | **Verified corrected.** Approved styles, order and historical provenance are retained. |
| **D3: retained weekly timecards disappeared after an EBM→WBM transfer** | Current SQLite, real PostgreSQL and Chromium/WebKit retained-scope workflows exercise the canonical transfer, both node views, prior weeks, Combo/Sort boundaries, current versus revoked authority, stale edits and rollback. Current area narrows authorization; historical slice area remains attribution. GETs are asserted read-only. | **Verified corrected.** No historical slice/attendance/Sort rewrite or transferred-employee access grant. |

## Current integrity and operational verification

The separate **231-case integrity run passes**, with 497 passing subtests and no skips. Explicit source/test coverage includes authorization revocation, atomic reset tokens, shared registration throttling, public boundaries, enforced headers/nonces, long-lived CSRF renewal, W1 live revisions/current-sort authority, explicit NeoErmac identity, native MotherBrain Block-Out and Rain load precedence/overrides, retained timecards, resource PROF, finalized SPEAR reconciliation and bounded SubZero archive discovery.

The **53-case clean PostgreSQL critical run passes**, with 126 passing subtests. Independent connections exercise password-reset serialization, concurrent authorization provisioning/registration, retained-scope locking/rollback, per-cycle resource evidence/completion accounting, repeated-destination identity, native/imported Block-Out races and W2 boundaries. Local PostgreSQL is **17.11**; production metadata reports **18**. Passing local PostgreSQL evidence is not a claim of identical platform coverage.

The original W3 cases and current resource-PROF cases preserve the five-stage fueling lifecycle, permanent work/visit identity, independent FUEL/UPLIFT/DEFUEL evidence, no fabricated Actual/transfer/ON, submission before Dispatcher completion and exactly-once inventory/events. Browser coverage includes the previously troublesome correction/reopen, visit, handoff/submission and personnel-swap workflows. Authorization and dirty-draft checks remain enforced. No production completion or fuel test was used.

| Fresh execution | Passed IDs | Failed IDs | Error IDs | Skipped | Not executed | Failure/error events |
|---|---:|---:|---:|---:|---:|---:|
| Original inventory | 367 | 0 | 0 | 0 | 0 | 0 |
| Current integrity | 231 | 0 | 0 | 0 | 0 | 0 |
| Broader application modules | 4938 | 15 | 0 | 899 | 0 | 23 |
| Bulk PostgreSQL-enabled matrix (initial cluster) | 250 | 39 | 7 | 0 | 534 | 53 |
| First critical PG attempt (initial cluster) | 34 | 16 | 3 | 0 | 0 | 22 |
| Clean critical PG cluster | 53 | 0 | 0 | 0 | 0 | 0 |
| Further PG operations/archives/deadlines | 105 | 3 | 0 | 0 | 0 | 3 |
| Combined Chromium/WebKit | 51 | 4 | 0 | 0 | 0 | 4 |
| WebKit unchanged login repeat | 0 | 1 | 0 | 0 | 0 | 1 |
| WebKit correct loopback HTTP environment | 1 | 0 | 0 | 0 | 0 | 0 |
| Unchanged Discharge repeat | 1 | 0 | 0 | 0 | 0 | 0 |
| PG connection-exception repeat | 1 | 1 | 0 | 0 | 0 | 1 |
| All tracked JavaScript | 402 | 0 | 0 | 0 | 0 | 0 |

Run scopes overlap and must not be summed into a unique application-wide total. Parent methods, subtests, setup/teardown errors and unexecuted IDs are separately recorded. Collected does not mean executed. Existing deprecation warnings remain visible; none were broadly suppressed.

### Broader-suite and PostgreSQL limits

The broader run completed every collected ID. Its strict unique totals are **4938 passed / 15 failed / 899 skipped / 0 unexecuted**. The bulk PostgreSQL-enabled attempt collected 830 IDs and ended with **250 passed / 39 failed / 7 error / 0 skipped / 534 not_executed**. Its not-executed IDs are explicitly enumerated in JSON; they are not passing evidence. The separate operations PostgreSQL scope has **105 passed / 3 failed / 0 error / 0 skipped / 0 not_executed**. Of the broader run's 899 skipped IDs, 343 were subsequently attempted in PostgreSQL sessions, 309 have at least one passing PostgreSQL-session result, and 556 were never exercised beyond the skip (including the Linux-only case). Earlier failures are not erased by that overlap accounting.

The broader command enumerates all **403 tracked top-level `tests/test_*.py` modules**. It finished in **3,047.03 seconds (50:47)**: 4,938 uniquely passing IDs, 15 uniquely failing IDs, 899 skipped, zero missing/unexecuted. Of the skips, 898 explicitly require disposable PostgreSQL and one requires Linux Gunicorn. Pytest prints 4,939 passing results plus 23 failure events because the Discharge parent also emits a passing outer call around its nine failing subtests; strict unique-ID accounting classifies that parent as failed. It excludes unrelated untracked prototype tests and runs browser tests separately. PostgreSQL-dependent cases skip explicitly when their disposable URL is absent; they are not called successful. The PostgreSQL-enabled bulk command includes imported SQLite fixture classes as well as actual PostgreSQL cases, so its total must not be described as that many distinct real-PostgreSQL concurrency tests.

Each broad/bulk session uses the established **60-minute limit**, with a graceful pytest deadline between cases rather than termination of an existing job. The broader run finished all collected IDs before that limit. Exact per-ID pass/fail/error/skip/not-executed outcomes and raw-evidence hashes are in the companion register. Test process-tree memory was sampled independently: the largest sampled audit tree was **4,056,719,360 bytes** during the broad run. This is a local test-process-tree sample, not a guaranteed peak or the production worker footprint. Launcher-only ~15 MiB values were excluded from resource claims. The broader run retained 1,411,494 warning events, predominantly existing deprecations; warnings were not suppressed.

### Additional failures, classification and intended-risk coverage

The broad run has **15 unique failed IDs and 23 failure events**: 11 Upcoming Actual setup failures, three scorecard evidence failures and one Discharge parent with nine subtest failures. The register identifies every one by exact node ID and retains exact additional PostgreSQL/browser failures, including failed first attempts and passing repeats. No original 367 ID regressed.

* **NeoErmac Upcoming Actual fixture regression:** eleven methods create lineup selections before creating an explicit Master/Mission identity. The W1 canonical shared helper correctly finds zero selectable Masters and rejects setup. This is a confirmed test-support ordering defect, not evidence that production destination fallback should be restored. The eleven later catch-all, rollback, permission and query assertions remain unexercised by these methods. Current explicit-identity, repeated-destination, stale/current-sort and browser cases pass independently; they do not erase this coverage gap.
* **Scorecard synthetic evidence:** three `ScorecardMathTest` methods use non-ORM version-3 events without `prof_participation`, yet expect numerical timing or a prediction-conflict reason first. `event_math()` correctly returns `PARTICIPATION UNVERIFIED`. Current canonical eligible and multi-resource exclusion tests pass. This is an incompatible evidence fixture/expectation; do not invent historical participation or weaken exclusion. The isolated math/conflict intentions still need valid synthetic provenance in a separately authorized test correction.
* **Discharge equivalence timing:** one parent method reports nine subtest failures comparing `updated_at` timestamps from sequential synthetic workflows. The unchanged standalone method passes, and a separate guarded comparison of 2/50/0 rows plus no-current-sort has no payload differences. This is suite-order/clock-sensitive test evidence; no data-integrity defect was demonstrated. Query/write expectations and post-commit Sort protections remain exercised, but a green isolated repeat is not a clean full-suite result.
* **Browser reliability:** the initial combined 55-method run has **51 passes and four failures**, with 117 passing subtests. Rain aged-navigation fails once with `ERR_ABORTED`; its unchanged isolated repeat passes. Two compact-Rain dirty/clear methods fail together around a fixed 200 ms intercepted-response assumption and a subsequent route/context callback; both pass unchanged in an isolated two-method run. WebKit Dispatch latency fails local login twice because the HTTP fixture inherits secure-cookie policy. The unchanged case passes with `SESSION_COOKIE_SECURE=0` only in its synthetic loopback HTTP environment. No production cookie setting or regression test was edited. All four methods have subsequent passing evidence, but the original combined run remains failed.
* **PostgreSQL continuation fixture/expectations:** two additional PostgreSQL-only inherited methods expect a primary WAITING FOR TRUCK label and exactly three action-center items, but receive MISSION DATA MISSING and four items. Their PostgreSQL setup creates a bare departure without the inbound/tail context present in the SQLite parent. Current status code intentionally retains missing-data priority and separates movement warnings; the busy-truck admission rejection itself already passed. The later release/action-center assertions remain unverified by these failing methods. This is an additional fixture/expectation issue with medium confidence, not proof that the availability guard should change. It remains explicit in the evidence register.
* **PostgreSQL test environment:** the first cluster has a small global fixture lock table (`max_connections=30`, `max_locks_per_transaction=64`) and encounters catalog shared-memory exhaustion during hundreds-of-table CREATE/DROP operations. Both Windows clusters also log child-process shared-memory address reservation error **487**, corresponding to intermittent loopback connection startup timeouts. Bulk concurrency waits and one extra FROST retry attempt are retained as failed evidence, not declared app defects without reproduction. A separate cluster with `max_connections=50`, `max_locks_per_transaction=512` passes all 53 critical cases. The further operations run and any precise repeats are recorded above/in JSON. The further 108-case operations run has **105 passes and three failures**: two connection-startup failures in SPEAR tests, and a forward-clock case that fails in its initial `seed_capture()` with a logged `OperationalError`, before the clock is changed. In the unchanged two-case SPEAR repeat, rollback/finalization passes and schema-bootstrap again hits a connection timeout. The separate operations run also passes the three-hour hourly-capture test that failed under the first cluster. Bulk FOB barrier and other synchronization failures remain explicitly unresolved at the method level rather than treated as successful concurrency evidence. These platform failures prevent a clean full-matrix PostgreSQL claim.

No application code, fixture or assertion was modified to make these results green. No test was deleted, marked expected-failure or broadly skipped by this audit.

## Production verification and performance limits

### Identity, availability and security

Before publication, Render reports the exact audited SHA LIVE. **40 bounded read-only HTTP checks pass**, including `/healthz`, `/login`, manifests/service worker/favicon and 35 static resources. All 35 static responses match committed Git bytes, including the manifest, 29 CSS fragments, shared CSRF/mobile/Rain scripts and font CSS. Login exposes the CSRF meta control and no inline handlers. Two additional login responses have different nonces.

Observed enforced CSP includes `default-src 'self'; script-src 'self' 'nonce-<per-response>'; script-src-attr 'none'; style-src 'self'; style-src-attr 'none'`, with the existing approved image/font/connect directives. It is an enforced `Content-Security-Policy` header, not merely Report-Only. HSTS is **`max-age=31536000; includeSubDomains`**. Current browser security cases independently test untrusted scripts/handlers, authorized assets and token renewal; production was not subjected to injection attempts.

Using the existing authenticated browser session, Portal, Gateway, NeoStaffing landing/reports/timecards and NeoRain inbound loaded without operational form submission. Observed console error lists on the checked Staffing/Rain views were empty. This is availability evidence only; synthetic browser/PostgreSQL tests provide mutation and authority evidence.

One bounded production FROST metadata SELECT found the **00:00 through 05:00 UTC November 4 hourly `auto_capture` receipts all `ready`**, one attempt and no retained error category. The 05:00 slot's earlier `Deferred` log therefore did not mean permanent loss; its receipt is ready with `updated_at=05:01:20.170Z`. This verifies stored delivery status under the existing read-back contract, not a new independent re-download of historical R2 evidence. No R2 object was modified or re-exported for this audit.

### Render observations

The historical metric window is **November 3 05:00 → November 4 05:00 UTC**, five-minute resolution, with **18 instance series**. Repeated deployments reset processes; these series cannot be stitched into one long memory plateau or compared to incompatible original-audit traffic windows.

A separate current-instance series covers **04:25 → 05:50 UTC**, 18 samples: memory rises from **208,506,880 to 331,251,700 bytes** (about 198.85→315.91 MiB), below the approximately 512 MiB limit. This is **85 minutes**, not a sustained plateau proof, leak proof or OOM forecast. CPU's reported maximum is **0.17907213 in the provider's `cpu` unit**; it is not converted into an invented percentage. The latency query returns no series, so no production p95 claim is made.

Application access logs, rather than the unavailable request-log feed, show **39 HTTP 429s** in the complete bounded **04:22:11→05:19:30 UTC** query: 36 `/neosektor/driver-routing/state`, two `/neosektor/ballmat/state`, one `/neosubzero/ucc/revision`. These are operational refreshes. The existing per-user/screen minimum-interval guard and Retry-After behavior remain enabled. Device-level cause and active-screen latency were not measured; this is not evidence that every original 429 was fixed or that any save was lost.

A complete bounded error-pattern query through **05:56 UTC** finds one **05:21:14 UTC** HTTP 500 from `/motherbrain/google-live-poll/execute`, accompanied by `GoogleMotherBrainReaderError`, `google_timeout`, stage `read_live_ranges`, and the existing safe rollback path. Nearby execute responses return 200. No traceback, OOM or worker-timeout entry appears in that query. A safe external failure is still a real failed response; the report does not claim zero production errors. Final documentation-rollout logs are checked separately after publication.

### Neon and deadline measurements

Read-only endpoint metadata confirms the actual production compute's **300-second suspend timeout**, 0.25–2 CU bounds. The project's creation default is not confused with that active endpoint setting. Period-to-date compute/active-time/transfer counters have no matched pre/post workload series; **actual CU-hour savings are unverified**.

Fresh deterministic one-hour simulations execute real SQLite SQL/services with fake clocks and provider storage. Their clock callbacks are not all database wakeups:

| Scenario | SELECTs | DB connections / transactions | UPDATE / INSERT | Commits | Longest DB-free interval |
|---|---:|---:|---:|---:|---:|
| Fully idle after current capture | 96 | 12 / 12 | 0 / 0 | 0 | 600 s |
| One scheduled hourly FROST capture | 131 | 29 / 29 | 10 / 1 | 13 | 600 s |
| One recovery expiry | 99 | 12 / 12 | 2 / 1 | 1 | 600 s |
| Pending document plus worker restart | 132 | 16 / 16 | 3 / 1 | 2 | 600 s |

The scheduled capture occurs exactly 1,800 seconds into its scenario. Expiration and durable restart/retry assertions run in their dedicated tests. The idle scenario has 120 lightweight clock callbacks but only 12 database activity phases and no writes/commits. **600 seconds exceeds 300 seconds when legitimate deadlines permit; real user traffic and provider work can keep production active.** These are isolated scheduling measurements, not measured Neon cost reductions.

Current query-budget/scale assertions in W2/W3/W4 original cases pass together. Finalized SPEAR/no-recurring-update and bounded archive cursor tests also pass in current integrity coverage. No production full-history scan, stress experiment, EXPLAIN ANALYZE mutation or claimed production CU reduction was used.

## Dependencies, compilation and evidence reproducibility

The exact local resolved inventory is retained with its SHA256 in the evidence register. `pip check` reports no broken requirements. An isolated declared `pip-audit 2.10.1` installation scans **72 local resolved dependencies**, finds **0 known advisories** and leaves production/application requirements unchanged. Patched local versions include Werkzeug **3.1.9** and pypdf **6.21.4**. The local Windows Python **3.13.14** environment differs from production Linux Python **3.14.3**; the scan is not a complete attestation of every deployed Linux transitive wheel or of unknown vulnerabilities.

All **380 application Python files compile**, **75 runtime JavaScript files pass Node syntax checks**, all **402 tracked JavaScript tests pass** (no skipped/cancelled tests), and all 29 CSS source identities match. Compilation and diff checks do not replace behavior tests.

The companion JSON contains exact case statuses, package ownership, rename, run counts, raw XML/log/event/argument hashes, manifest identities, production HTTP/metric summaries, Neon metadata and preservation results. Raw diagnostics, screenshots and command outputs remain local under the separate ignored audit directory; raw production access metadata and synthetic CSRF/session tokens are not published. The recorded commands use `.venv/Scripts/python.exe`, `-p task15_guard`, isolated SQLite URLs or loopback-only PostgreSQL URLs, `AUTO_BOOTSTRAP_DATABASE=0`, and bounded per-session deadlines. Reproduction of the full suite requires accounting for the documented Windows PostgreSQL and HTTP-cookie environment differences.

## Remaining risk register and sign-off conditions

| ID | Severity / confidence | Unresolved scope and operational impact | Condition / disposition |
|---|---|---|---|
| F1 | Medium / High | Eleven Upcoming Actual setups, three raw scorecard fixtures and two inherited PostgreSQL continuation assertions lack the required fixture context for their intended positive assertions. Original 367 are closed, but broader regression confidence remains incomplete. | Valid canonical test setup/provenance and a clean combined execution are required for unconditional test sign-off. No destination fallback or participation weakening is justified. |
| F2 | Medium / High | Combined browser timing/order failures, the Discharge clock-sensitive comparison and local HTTP cookie mismatch; dirty-draft and latency coverage is not consistently green in one command. | Retain all failures and passing repeats; stabilize the isolated harness in a separately authorized scope. No production UI defect demonstrated. |
| F3 | Medium / High | Full broad/PostgreSQL matrix is not cleanly certified within resource/platform limits; unexecuted/skipped IDs remain explicit. | Complete outstanding scope on suitable PostgreSQL/Linux resources and resolve any independently reproduced app issue before claiming whole-suite PASS. |
| F4 | Medium / High | Current-release memory increases during a short window; sustained headroom/plateau is not established. | Comparable sustained measurements are needed. No hosting change, OOM claim or theoretical savings declaration is authorized by this audit. |
| F5 | Low / High | Operational 429s and one bounded Google timeout/500 remain observed; end-user delay is unmeasured. | Keep rate-limit/timeout guards and existing recovery; observations do not establish a mutation-integrity failure. No automatic retry or new optimization is initiated. |
| F6 | Informational / High | Actual Neon CU savings, production p95 and complete deployed dependency inventory are unavailable. | State these as unverified; do not use isolated improvements as production cost/performance certification. |

**Final sign-off is CONDITIONAL PASS.** Continued operation is supported by the complete original inventory, repaired D1–D3, focused security/authority and real transaction tests, successful representative workflows and current production availability. Unconditional full-system PASS is withheld for the explicit test and measurement limitations above. No confirmed unresolved Critical/High application issue is hidden as test cleanup; if a future isolated reproduction demonstrates one, this decision must be revisited. This audit ends at publication and verification and does not authorize or begin another package.

## Documentation publication

Only final audit Markdown/JSON are committed. `git diff --check`, preservation verification and staged-path inspection are required before push. Render autoDeploy is allowed to run; no manual deploy or configuration change is made. Exact documentation commit SHA, resulting LIVE deployment ID, startup/worker logs, repeated health/login/security/asset checks and final rollout observations are supplied in the completion receipt after the self-referential commit exists. The audited application/test bytes remain the SHA identified above.
