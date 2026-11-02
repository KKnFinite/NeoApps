# NeoApps final post-remediation audit — 2026-11-02

## Executive summary and scope

Decision: **FAIL — a confirmed material operational-authority defect remains.**

The reviewed remediations materially improve authorization, account recovery,
browser security, evidence durability and idle database behavior. This audit
re-read current implementation and executed new isolated checks; previous
completion messages were not accepted as proof. No application correction was
made during this audit. An isolated reproduction through MotherBrain’s actual
manual editor and Google Rain importer demonstrated that an authoritative manual
Block-Out can be overwritten (R07). That integrity defect blocks final sign-off.
Two external-service resource gaps and unresolved broad regression failures also
remain. A healthy deployment does not resolve those findings.

This is not authorization to launch an experimental FROST model, certify
physical frost accuracy, or change operational authority. The risk register
separates confirmed defects, test problems, measurement limitations and optional
improvements. No Critical vulnerability was established in the tested paths.

### Repository and deployment identity

- Fetched `origin/main`; local main was already synchronized.
- Audited application/source SHA: `7aad361c65f230e34375e8bee77d77d068a2a870`.
- Production SHA independently confirmed at audit start: the same SHA.
- Starting LIVE deployment: `dep-dbk1jp7avr4c73ck7mvg`, LIVE
  `2026-11-02T05:06:24Z`.
- GitHub repository: `KKnFinite/NeoApps`.
- Render service: `srv-d8buq2mgvqtc73e375j0`, one instance, `0.5c-512mb`;
  start command `gunicorn run:app --bind 0.0.0.0:$PORT`; no preload flag.
- No applicable AGENTS.md was found in the repository or its inspected parents.
- Six pre-existing untracked artwork/prototype/audit items were inventoried and
  excluded. No branches, stashes, application files, dependencies, credentials,
  production data, settings or archived objects were changed by the auditor.
- Only this report and its non-operational evidence summary are intended for
  commit. The documentation commit and its AutoDeploy receipt are reported in
  the completion response: a commit cannot contain its own SHA or future deploy
  ID. The application source under audit remains the SHA above.

The standalone original N01–N20 audit report was not present among tracked
documents. The supplied finding definitions, original pre-remediation revision
`11a85790`, actual remediation commits through `7aad361c`, current source,
test implementations and available Task 15 evidence were inspected. This is a
limitation on comparison to the original narrative, not on current source access.

## Original findings register

Status describes the stated defect within the evidence scope, not a claim that
every endpoint or every dependency is safe. Source references are relative to
the audited SHA. The final task's numbering is used: N14 SDK lifecycle, N16
weather backoff, N17 response limits. Earlier Task 8 used different ordering.

| ID | Status | Independently inspected implementation and evidence |
| --- | --- | --- |
| N01 | Verified resolved | `app/services/access_control.py:623,778,903`: reinstatement changes an inactive grant to pending, retains prior decision metadata, and does not authorize it. Denied grants stay denied. `tests/test_authorization_revocation.py` covers requests, routes and legitimate grants. |
| N02 | Verified resolved | `access_control.py:197,447,661`: role lookup includes inactive rows; explicit disabled roles return denied before fallback. Seed inserts missing identities only, does not elevate existing roles, and rejects overwrite mode. Lightweight projections also retain inactive rows. Revocation and PostgreSQL provisioning checks exercise these paths. |
| N03 | Verified resolved | `access_control.py:377`: initialization creates genuinely missing definitions and maintains metadata without setting existing activation flags. Gateway/node authorization checks global activation independently of role records. |
| N04 | Verified resolved | `app/__init__.py:630,667,715` and runtime environment resolution enforce CSP/HSTS on Render. Live `/healthz`, `/login`, `/create-account` returned enforced CSP and HSTS, with unique nonces. The policy blocks inline handlers and arbitrary scripts; no response-wide nonce rewriting remains. |
| N05 | Verified resolved | `app/services/csrf.py:68,112`, `app/static/js/csrf.js:52`: expiry and same-session signatures remain checked; renewal accepts only same-session proof and same-origin/session checks. Shared fetch resolves the current token, native forms retain their submitter and identity, and mutations are never automatically replayed. Shift, dynamic-form, two-tab, draft and failure browser cases ran under enforced CSP. |
| N06 | Verified resolved | `app/services/user_tokens.py:66`, `app/auth/routes.py:418`: lock account first, refresh token state after the lock, conditionally consume unexpired/unused token, update password and revoke sibling tokens in one transaction. Invalid passwords and database failures roll back. PostgreSQL races test both the same token and distinct links for one account. |
| N07 | Verified resolved | `app/services/auth_rate_limits.py:136`, `app/auth/routes.py:277`: separate durable registration admission locks IP then identifier before password work or delivery. Defaults: 120/IP and 5/normalized identifier per 3,600-second window, generic 429 plus Retry-After, HMAC subjects, no blocked email. PostgreSQL concurrency/recreated-connection tests and browser registration were rerun. |
| N08 | Verified resolved | `access_control.py:872`: unique identities plus bounded SAVEPOINT conflict recovery preserve the caller transaction and reuse the winner without rewriting it. Explicit SQLite BEGIN prevents SAVEPOINT release from committing provisioning independently. Database constraints remain authoritative. |
| N09 | Verified resolved in code and isolated PostgreSQL; actual Neon suspension unverified | `app/services/background_deadlines.py:18,36,179`, `neostaffing_employee_recovery_worker.py:11`: UTC-aligned 600-second safety discovery, actual retry/expiry/hour deadlines, commit hints, restart recovery, no idle LISTEN connection. Fresh instrumentation demonstrated a 600-second SQL-free interval with FROST enabled. This establishes compatibility with a 300-second idle threshold, not measured Neon suspension or CU savings. |
| N10 | Verified resolved | `app/services/neoscorpion_spear_decisions.py:391`: durable `reconciled_at`, bounded unresolved-only claim and exact physical-work sealing. Ten further reconciliations of a finalized decision produced ten SELECTs and zero INSERTs/UPDATEs; pending R2 delivery does not reopen it. Partial index verified with EXPLAIN. |
| N11 | Verified resolved | `app/services/neosubzero_archive_reconciliation.py:18,91`: durable gateway cursor/checkpoints, keyset history, recent/audit/retry slots, revision inventories and safety sweeps. At most ten candidate operations per call by constants; measured batch examined six. No unchanged receipt rewrites or historical payload reconstruction after discovery. Tests cover legacy gateway code, corrections, missing receipts and competing workers. |
| N12 | Verified resolved | `app/services/neostaffing.py:3428`: grouped scalar SQL counts assignments by unit/classification, filtering active person and assignment; no person hydration/cache. Fresh PostgreSQL: five leaders 6→1 SELECTs; fifty 51→1; results equivalent and no writes. |
| N13 | Verified resolved in targeted transports | `app/services/neoscorpion_learning_vault.py:88`, `app/services/neosubzero_archive.py:58`, `app/services/frost_vault.py:124`: independently bounded GET/read-back, canonical checksum/schema/identity validation, immutable conflict rejection, paired delivery and attempt/audit fencing. Legacy accepted-only receipts are not relabeled verified. Mock outage/corruption/restart tests plus one live FROST hash-verified GET support this status. No auditor R2 PUT occurred. |
| N14 | Partially resolved | SPEAR/FROST owned client scopes and streams close on normal/failure/nested paths (`neoscorpion_learning_vault.py:32`, `frost_vault.py:23`). Additional NeoRain diagnostic client ownership lacks explicit cleanup, including partial construction: R02 below. No claim of a demonstrated long-term SDK leak is made. |
| N15 | Verified resolved | `app/services/neoscorpion_spear_learning_capture.py:38`, completion call sites in `neoscorpion.py:3476,4137`: immutable snapshot/outbox intent in the canonical transaction; R2 only after a durable fenced claim in `neoscorpion_spear_decisions.py:495`. Provider failure neither waits on nor undoes completed operations. Failed local persistence safely rolls back rather than committing completion without intent. |
| N16 | Verified resolved | `app/services/neosubzero_weather.py:354`: independent METAR/points/forecast failure state, process-local in-flight coalescing, 60/120/240/480/900-second capped cooldown, success reset, unchanged success TTLs/stale expiry. Missing weather stays unavailable. Deterministic recovery/concurrency tests were rerun. |
| N17 | Partially resolved | Flight and normal weather paths use `bounded_http.py:16`: max+1 streaming, partial-read handling, independent wire/decompressed caps, valid JSON shape and closure. Remaining HIBP text and NeoRain diagnostic JSON paths lack equivalent pre-buffer bounds: R01. |
| N18 | Verified resolved for NeoSektor | `app/services/neosektor_sheets_compat.py:156,210,1189`: shared finite five-second timeout convention, separately bounded authentication, one owned client/workbook per operation, borrowed ownership preserved. Ordered writes stay unverified/pending on uncertainty, without replay or authority substitution. Mock transport/partial-write tests exercise the actual factory. No production Google test writes were sent. |
| N19 | Verified resolved within tested coordination modes | `app/static/js/live_revision.js:1`, `app/services/live_screen_refresh.py:233`: opaque session/user/gateway scope, URL/sort partition, revision-only messages, bounded Web Lock wait and network timeout, independent fallback, Retry-After cooldown. No shared HTML/actions/tokens. JavaScript tests and browser draft/COMPLETE checks exercise this; unsupported coordination may correctly produce duplicate GETs. Production 429 savings are not established by the short low-traffic sample. |
| N20 | Verified resolved for audited exact inventory | `requirements.txt`, `docs/audits/2026-11-01-dependency-inventory.json`: deployed Werkzeug 3.1.9, pypdf 6.21.4, Pillow 13.0.0; all 50 packages matched the install log. Fresh pip-audit: zero known advisory matches, no skipped packages. Future floating resolutions, OS/browser/toolchain security remain outside this claim. |
| A01 | Verified resolved | `parking_plan.py:595,666`, `google_motherbrain_parking.py:125`: Google-specific normalized effective-placement no-op retains timestamps/attribution; explicit manual edits and required promotion/alerts remain effective. Six tails × ten identical snapshots: 60→0 assignment UPDATEs. |
| A02 | Verified resolved for transfer reduction; render opportunity remains | `neoscorpion_dispatch_fragments.py:14`, `neoscorpion/routes.py:329`: authorized, sort-checked fragment response with bounded fingerprint metadata omits unchanged markup; full initial rendering remains. The server still renders rows to calculate fingerprints: optional R05. Browser byte/COMPLETE latency measurements are below. |
| A03 | Verified resolved | `app/__init__.py:715`, trusted template-owned `csp_nonce()` attributes. Fresh unpredictable 32-byte nonce per response; injected scripts/handlers do not acquire trust. Browser checks use actual enforced CSP, without unsafe-inline/unsafe-eval. |

## Security assessment

The reviewed authorization gates are server-side. Request caches include the
actual request identity (`app/services/request_cache.py:11`) and clear after
transaction boundaries; no process-global permission cache was found in these
paths. Disabled rows are distinguished from missing rows. A reinstatement's
`is_active=True` is a queue representation, not an approval: pending status still
denies access. Original approvals/denials are retained in transition metadata.

Password reset serializes issuance, consumption and sibling-token invalidation
through the same account lock. Password policy/session-version rotation remain
inside the successful password transaction; old sessions fail subsequent checks.
Registration reserves its shared allowance before hashing and preserves pending
access. Trusted-proxy resolution does not accept arbitrary forwarded headers by
default. The actual production proxy allowlist was not requested or inferred.

CSRF renewal neither extends unsafe-request TTL indefinitely nor authenticates a
stale session. No POST replay is attempted after an uncertain response. Public
HTML responses had fresh CSP nonces; HSTS was
`max-age=31536000; includeSubDomains`. Observed CSP included
`script-src 'self' 'nonce-<per-response-value>'`, `script-src-attr 'none'`,
`object-src 'none'`, `base-uri 'self'`, `frame-ancestors 'self'`,
`form-action 'self'`, `connect-src 'self'`; it was not Report-Only.

Representative permission/current-sort/mission ownership tests and archived
evidence authorization were inspected and executed. Native-time precedence
passed in the primary Google importer, but **failed in the separate Google Rain
importer**: see R07 and its direct canonical-writer reproduction below.
This is not exhaustive endpoint-by-endpoint penetration testing or proof against
all IDOR/XSS paths. No real employee, account, aircraft or Google mutation was
used to test those guarantees. Service-worker code restricts caching to static
assets and fetches navigations without cached authenticated HTML
(`app/static/service-worker.js:38`).

## PostgreSQL and Neon efficiency

Measurements below are fresh isolated PostgreSQL 17.11 executions, not Neon
production CU measurements. Small fixtures, local disk, instrumentation and
concurrent non-load-test activity affect timings. Query counts are more portable
than these timings; no capacity or monetary savings guarantee follows.

| Workload | Independent measurement |
| --- | --- |
| Finalized SPEAR, ten later due intervals | 10 SELECTs, 0 UPDATEs, 0 INSERTs; 48.833 ms total. |
| SubZero, 120 operations, unchanged pre-N11 algorithm loaded from commit `11290a85` | 962 SELECTs, 0 writes, 120 payloads; 381.673 ms. The separate initial seeding run inserted 120 receipts. |
| SubZero first bounded pass with those receipts already present | 78 SELECTs, 1 cursor UPDATE, 7 cursor/checkpoint INSERTs, 6 payloads, 117.445 ms; no receipt rewrites. This initialization cost is explicitly separate from the warm comparison. |
| SubZero after bounded discovery | 17 SELECTs, 1 necessary cursor UPDATE, 0 receipt INSERTs/UPDATEs, 0 payloads; 56.887 ms; 6 operations examined. All 120 receipt identities retained after repeated bounded passes. Tests also cover a 240-operation population. |
| Six parked tails, ten identical Google applications | Legacy same-slot rewrite behavior: 400 SELECTs, 60 assignment UPDATEs, 870.343 ms. Current: 400 SELECTs, 0 UPDATEs/INSERTs, 744.574 ms. No claim that A01 removed the remaining reads. |
| Five leadership assignments | Frozen former algorithm 6 SELECTs, median 2.190 ms; current 1 SELECT, median 0.475 ms; equivalent result, 0 writes. |
| Fifty leadership assignments | Former 51 SELECTs, median 15.058 ms; current 1 SELECT, median 0.542 ms; equivalent result, 0 writes. Five cold-session samples per algorithm. |
| 60 simulated idle minutes, current FROST slot already archived but collection enabled | 120 memory/clock wakeups; only 12 database work cycles/connections/transactions, 96 SELECTs, 0 writes/commits; longest database-free interval 600 seconds. |
| 60 simulated minutes, one FROST capture due | 12 database work cycles, 28 connection acquisitions/transactions, 131 SELECTs, 3 INSERTs, 10 UPDATEs, 13 commits; capture at simulated second 10 after startup; longest SQL-free interval 600 seconds. The capture's legitimate work is not removed. |

EXPLAIN (without ANALYZE) on the isolated database selected index-only scans on
`ix_spear_decision_unresolved` and `ix_subzero_archive_gateway_cursor` for the
actual due-decision and keyset shapes. A small-fixture plan is not a production
plan guarantee. No production EXPLAIN ANALYZE, extension, migration, index,
historical scan or data repair was executed.

Local commit hints only shorten schedules; durable receipts, UTC-aligned safety
checks and restart discovery recover missed hints/other workers. Expiry code
retains bounded batches, exact expiry comparisons, encrypted recovery payloads
and SKIP LOCKED. The safety checks cannot guarantee deadlines during a prolonged
database/service outage. Pool pre-ping occurs on checkout, not via an idle
database timer (`app/config.py:208`).

The connected Neon scope exposed project `NeoGateway`, production branch
`br-aged-fire-aj4msg03`, database `neondb`, and the expected FROST/SPEAR tables.
Endpoint `ep-small-waterfall-ajjd0p6e` independently reported a **300-second**
suspend timeout, active state, 0.25–2 CU range and PostgreSQL 18. Its actual
endpoint setting takes precedence over the different project default. Read-only
activity metadata showed two idle client connections and the auditor's active
zero-second metadata query; no idle-in-transaction client was present in that
snapshot. These SQL reads themselves count as database activity and were not
used to pretend the service was idle.

`pg_stat_statements` was absent. The read-only calls diagnostic reported that
prerequisite; no extension was installed. Consequently production query-frequency
comparisons, cumulative application SQL cost and before/after CU savings remain
unavailable. The project returned billing-period counters (November 1–December 1)
`compute_time_seconds=26243` and `active_time_seconds=104592`; these are not a
before/after experiment or proof of successful suspension. The local test engine
was PostgreSQL 17.11, so exact PostgreSQL 18/Neon planner equivalence was not
established. No production connection string or user data was retrieved.

## Render performance, reliability and FROST

Read-only Render metadata confirmed the existing 512 MiB / 0.5 CPU service,
one instance, automatic commit deployments and canonical bootstrap command.
Gunicorn's existing post-worker startup still owns recovery/archive initialization;
no preload or additional service was introduced.

Available one-minute metrics for 04:15–05:13 UTC on November 2:

- Previous instance `xx4dv`: 53 memory samples, 273.79 MiB first,
  328.33 MiB last, 328.40 MiB maximum.
- Audited release instance `7rggp`: seven samples, 189.57–204.43 MiB.
- Largest returned CPU bucket: 0.06267342 CPU, about 12.53% of the 0.5 CPU limit.
- Instance-count series remained one. The deployment replacement is known;
  this is not proof that no worker restart ever occurred.

A follow-up 05:10–06:08 UTC series on the same `7rggp` instance contained
59 memory samples: 204.41 MiB first, **305.65 MiB** maximum/last; largest returned
CPU bucket 0.0960804 CPU, **19.22%** of the 0.5 CPU allocation. Instance count
remained one. It includes the natural 06:00 capture. Memory was below the HRRR
admission ceiling at the sampled points, but continued to rise across this
warm-up window: **a stable plateau or sustained memory stabilization was not
established**.

These are Render's returned memory series, not independent cgroup RSS/PSS or
high-frequency peaks. The old instance's growth cannot be declared a leak or
stable plateau from this window. The fresh release's low startup memory cannot
prove sustained headroom. Memory/CPU pressure admission remains cooperative,
not a reservation against allocations made by an arriving operational request
(`frost_resources.py:1`). All original 75%/128 MiB and HRRR extra-32 MiB guards
remain in code and are exercised by tests.

For 2026-11-01 05:00 through 2026-11-02 05:25 UTC, the returned hourly HTTP
series contained totals 713 HTTP 200, 5 redirects, 536 HTTP 304, 41 HTTP 404 and
one HTTP 499. No 429/500/502/503 series was returned. The error-level log query
returned zero entries. HTTP latency data was empty, so no production p50/p95/p99
is claimed. This roughly day-long, low-traffic window spans deployments and is
not comparable to the original seven-day 2,255-429 sample. It does not establish
multi-user capacity, request savings, absence of all log failures or cost savings.

Authenticated FROST status at 05:17 UTC, followed by a fresh 06:08 UTC read,
independently showed:

- Automatic collection enabled/active; 97 archived capture slots, 0 pending,
  0 failed, 9 accumulated missed intervals.
- Eight registered research artifacts; 464 scored and 312 unavailable prediction
  records across the archive. These are prediction records, not independent nights.
- Latest 24 consecutive returned hourly slots: all 24 verified; 22 with eight
  scores each, two with eight UNAVAILABLE records each. At 02:00 the source
  cycles were unavailable; at 05:00 memory protection rejected HRRR. No zero
  score was fabricated for either condition.
- The natural 06:00 slot attempted at 06:00:00.167830, captured at
  06:00:37.086205, committed at 06:00:37.165045 and verified off-site at
  06:00:38.143016 UTC. All eight scores were present, using HRRR cycle 04:00 UTC
  and threshold 0.5; next slot 07:00 UTC. The browser had been closed before
  06:00 and no manual capture was invoked. Opening VIEW performed a fresh
  bounded private-object read-back and displayed “Private evidence verified.”
- Read-only GET of captured object 2934 performed the route's bounded R2 read,
  decompression and stored checksum verification. It returned eight exact frozen
  model hashes matching `app/data/frost_prospective_research.json`, thresholds
  0.5, KRFD identity and distinct H0/H1/H3/H6 entries. `approved`,
  `operationally_validated` and `physical_frost_probability` were all false.
- Snapshot captured 04:00:37.685611 UTC and committed 04:00:37.789890 UTC;
  source cutoff 04:00:36.719452 UTC. H0 is current-condition recognition.
- 11 original documents and 36,614 deduplicated Xchange records remained cataloged.
- Departure discovery reported complete, 204 qualified actual departures with
  UNKNOWN treatment outcomes, 1,102 requiring review, zero matched treatments,
  zero verified physical-present/absent or no-spray outcomes in that correlation
  population. Archived revision counts are not distinct departure counts.

No browser-triggered capture, model approval, R2 test write or Google mutation
was performed. There is still no physical-outcome population from which to
claim independently validated aircraft frost probability or false-positive rate.

## Operational and automated regression results

The full Python attempt **completed**, rather than stopping at collection:
367 tracked non-browser modules, a 60-minute subprocess limit, 44 minutes
30.88 seconds reported by pytest (45 minutes 00.99 seconds launcher wall time).
Its verbatim summary was:

```text
799 failed, 4475 passed, 746 skipped, 1350690 warnings,
2925 subtests passed in 2670.88s (0:44:30)
```

The 799 figure is **failure events including subtests**, representing **367
unique failing case IDs**, not 799 distinct test methods. JUnit totals were
8,945 entries, 799 failures, zero collection/errors and 746 skips. Do not add
pytest and JUnit/subtest totals as if they were independent test populations.

| Separate execution | Actual result |
| --- | --- |
| Focused remediation Python group | 181 passed + 362 passing subtests, no failures/skips; 75.14 s. These overlap the broad suite. |
| Fresh isolated PostgreSQL group | 188 passed, no failures/skips; 702.85 s. Atomic reset/registration/provisioning, archive fencing, restart/deadline, parking, leadership, recovery, fragment and departure cases included. |
| JavaScript | 396 passed, 0 failed/skipped; 1.002 s. |
| Browser initial / unchanged replay | 13 passed, 3 failed + 50 passing subtests / 1 passed, 2 failed. |
| Controlled local-HTTP WebKit rerun | Both previously failing HTTP-cookie fixtures passed unchanged; 74.57 s. |
| Additional Chromium/WebKit employee recovery | 2 passed; 59.17 s. |
| Compilation, package consistency, syntax, whitespace | Python compilation, `pip check`, all 144 JavaScript syntax checks and `git diff --check` passed. |

Eighteen unique top-level browser cases passed **across these runs**; this was
not one clean 18-case execution. The full-suite 746 skips comprised 745 tests
requiring explicitly configured disposable PostgreSQL and one Linux-only
Gunicorn case. The separate 188-case PostgreSQL group does not imply all 745
optional cases ran. Initial PostgreSQL attempts had a wrong local port and
insufficient synthetic-cluster lock capacity; those setup failures and one
interrupted attempt are not counted as application defects or clean results.
The fresh local cluster was constrained to loopback, 16 MiB shared buffers and
20 connections; both synthetic PostgreSQL servers were stopped afterward.

The full run emitted 1,350,690 warnings, principally deprecated datetime/legacy
ORM use. They were not suppressed. Sampled local pytest RSS was approximately
3.4 GiB with about 33 GiB workstation memory free; this was a single test
process, not a load test, WSL workload or Render-memory measurement.

The supporting [evidence register](2026-11-02-final-post-remediation-evidence.json)
retains **every failure occurrence and skipped case**, module manifest, exact
summaries, classifications and raw-log/XML digests. Most remaining assertion
failures have **unresolved cause**; they are not labeled obsolete merely because
they predate this audit. This is an explicit limitation, and the baseline is
**not restored**. Failures span Ermac mission/pull/route budgets, MotherBrain,
Scorpion lifecycle/report projections, Sektor query/navigation budgets, Staffing
views and static markup/assets. No missing Python dependency or blocked external
network call was identified as the explanation for this full failure set.

Five selected failures were rerun independently and all five failed again:

- **Actual application defect, R07:** Google Rain overwrites native Block-Out;
  the canonical writer/import probe below confirms application impact.
- **Broken Google fixture:** the fake response lacks required
  `Outbound!Q3:Q50` removed-row evidence. This is not a production 403 diagnosis.
- **Broken Scorpion fixture:** `test_complete_route_commits_once` fabricates
  positive T/F without assigning the truck required before OFF. The real guard
  correctly rejects it. Other Scorpion failures are not automatically attributed
  to that one fixture.
- **Outdated schema instrumentation:** the test replaces every `Table.create`
  call with an exception even when `checkfirst=True`. An SQL listener on an
  already-created schema saw **zero CREATE TABLE statements**; two employee
  recovery triggers were intentionally reinstalled. Do not remove those guards.
- **Query-budget failure, R08:** six imported aircraft produced seven mission
  SELECTs instead of one. A separate 6/18-aircraft probe established scaling;
  the per-row explicit-authority locks are legitimate and must be preserved.

Other static/CSP/asset, permission, identity, query-budget and lifecycle failures
remain visible in the evidence register pending individual contract review.
No mass assertion update, broad skip or application correction was made.


No test or assertion was edited, deleted, broadly skipped or warning-suppressed
by this audit. Tracked non-browser test modules were selected from Git; the
untracked NeoRain prototype was excluded as unrelated work. Functional Python
tests had a non-loopback socket guard and explicit synthetic database settings.
Browser tests used local accounts/databases and mocked integrations, not the
owner's production account. Compilation, pip check, JavaScript syntax and
`git diff --check` ran separately.

### Browser failure classification

Initial 16-case Chromium/WebKit run: 13 passed, 3 failed, 50 subtests passed.
An unchanged three-case rerun passed the Ermac workflow but repeated both
WebKit login failures. The isolated launcher had set `DATABASE_URL` to SQLite;
`Config.SESSION_COOKIE_SECURE` defaults from its presence (`app/config.py:175`).
Two HTTP fixtures did not override it. Chromium loopback behavior masked the
problem; WebKit navigated back to `/login?next=/portal`. A controlled fresh run
with `SESSION_COOKIE_SECURE=false` solely for those local HTTP fixtures passed
both unchanged tests (74.57 seconds). Production HTTPS/cookies were not changed.
Classification: **environment/test-harness failure**, not a production login fix.
The Ermac console message about a live request during navigation did not repeat;
its root cause remains unconfirmed, not declared repaired.

Coverage included enforced CSP/trusted-versus-untrusted scripts, long-shift
native/AJAX/auto-save/dynamic submissions, two tabs, registration, portal/admin,
MotherBrain, Sektor controls, Ermac flight identity, Rain inbound/outbound,
Scorpion Dispatch/Fueler/Hanzo, SubZero Operations/UCC/FROST and Staffing.
Report/PDF, leadership desktop/mobile and deice FIN/CLEAR during mocked storage
outage cases were included. These tests establish their actual exercised paths,
not every possible role, screen size or simultaneous-operator combination.

Fresh critical COMPLETE measurements (two-row synthetic fixture, uncompressed
HTTP response bytes, identical 5-second cadence and dirty-row assertions):

| Engine | Broad pre-delta response | Compact response | COMPLETE visible before / after | Full-panel GETs |
| --- | --- | --- | --- | --- |
| Chromium | 62,996 bytes | 53,209 bytes | 5.004 / 5.000 s | 0 / 0 |
| WebKit, valid local HTTP cookie fixture | 62,996 bytes | 53,209 bytes | 4.923 / 4.944 s | 0 / 0 |

The measured transfer reduction is about 15.5% for this small changed fixture;
it is not an estimate for a 600 KB production panel. Fingerprint unit cases
also verify ten unchanged polls omit mission/assets markup. The full panel
remains available for actual full refreshes. Draft preservation and COMPLETE
eligibility assertions were retained throughout.

Fresh deterministic six-round revision measurements used the current browser
controller with native coordination supported versus its independent fallback:

| Equivalent tabs | Independent GETs / bytes | Coordinated GETs / bytes |
| --- | --- | --- |
| 1 | 6 / 858 | 6 / 858 |
| 2 | 12 / 1,716 | 6 / 858 |
| 5 | 30 / 4,290 | 6 / 858 |

These are counted mocked revision responses, not production bandwidth estimates.
The separate 429 test generated one request across five tabs, none during the
12-second Retry-After interval, and one after eligibility resumed. Unsupported
coordination deliberately falls back to correct independent reads. No mutation
is shared or replayed.

### Confirmed native-authority defect — R07

`app/neomotherbrain/routes.py:5618` applies the actual mission editor form;
lines 5675–5681 record manual Block-Out and source `manual`, without a Rain
override row. The isolated probe used `_mission_form_from_model()` and
`_apply_mission_form()`, committed, and invoked the real Google Rain milestone
applier with a subsequent conflicting source time:

| State | Actual Block-Out UTC | Source | Explicit Rain overrides |
| --- | --- | --- | --- |
| After MotherBrain editor | 2026-08-11 07:00 | manual | 0 |
| After Google Rain importer | 2026-08-11 07:45 | google_rain | 0 |

`google_rain_live_milestones.py:192` checks explicit Rain override presence.
Its `_apply_owned_timestamp()` at **294–312** then overwrites covered fields
without preserving an existing native/manual source. `neorain_overrides.py:14–23`
includes official Block-Out in those fields. The before-flush guard at line 51
protects existing override rows, not a native edit that created no override.
By contrast, `google_motherbrain_live_missions.py:615–624` preserves native
sources in the primary importer. The independent Rain step at
`google_motherbrain_live_poll_execution.py:229–270` can subsequently bypass that
protection in supported Google/mirror modes.

This is confirmed through a canonical writer, rather than assuming that a test
manually setting a source flag represents an actual supported workflow. The
existing `test_native_and_unattributed_milestones_are_protected` fails for the
same reason. The behavior entered with commit `2c05915a`, an ancestor of the
original audit SHA `11a85790`; it is **not attributed to FROST or Tasks 1–15**.
No production overwrite was proved or simulated. Its availability depends on
the enabled import mode and source data.

An independently authorized correction should unify native-source protection
across both importers, preserve explicit Rain override/correction/withdrawal
semantics and serialize concurrent edits. Tests must start from each canonical
native writer and demonstrate conflicting Google updates cannot change its
Block-In/Out. The permission-protected edit route at
`app/neomotherbrain/routes.py:3263–3328` calls this writer without a separate
integration-mode authority guard. Historical repair would require a separate evidence review and
authorization. No patch or historical repair was attempted in this audit.

## Dependency verification

The actual Render install list, not the workstation's environment, supplied
the 50 exact runtime versions. It matched the Task 15 inventory completely.
Fresh pip-audit 2.10.1 queried PyPI advisories for every enumerated direct and
transitive version using `--no-deps --disable-pip`: zero known matches and no
packages skipped. Local `pip check` also passed. There was no dependency update
or install in this audit.

- Werkzeug 3.1.9 satisfies the Windows-specific
  [device-name advisory](https://github.com/pallets/werkzeug/security/advisories/GHSA-g6x2-hccm-hh4m).
  Linux Render is outside that specific exploitation condition.
- pypdf 6.21.4 satisfies the
  [page-tree memory advisory](https://github.com/py-pdf/pypdf/security/advisories/GHSA-cggf-277g-3q54)
  and excludes the additional affected 6.21.3 release.
- Pillow 13.0.0 satisfies the
  [decoder advisory](https://github.com/python-pillow/Pillow/security/advisories/GHSA-4558-3h8r-v8xp)
  cited during Task 15.

The exact package list remains in `2026-11-01-dependency-inventory.json`;
scan counts and current test evidence accompany this report. Advisory matching
does not prove exploitability or vulnerability freedom. Floating dependencies
can resolve differently on the documentation AutoDeploy; its actual install
list must be compared again before the completion response. OS, browser binary,
build image, future advisories and all optional research packages were not
certified by this production-inventory scan.

## Remaining risk register and priorities

| ID | Severity / confidence | Component and evidence | Impact and smallest recommended correction | Complexity | Sign-off effect |
| --- | --- | --- | --- | --- | --- |
| R07 | **High / Confirmed** | `app/neomotherbrain/routes.py:5675–5681`; `google_rain_live_milestones.py:294–312`; primary importer guard at `google_motherbrain_live_missions.py:615–624`. Canonical manual editor/import reproduction changes 07:00/manual to 07:45/google_rain without a Rain override. | Authoritative aircraft departure time/source can be replaced by a lower-priority imported fact, affecting operational history and downstream departure evidence. Preserve native precedence in every importer while retaining explicit override/upstream revision behavior. No production repair without separate evidence/authorization. | Medium, narrow but concurrency-sensitive | **Blocks final sign-off.** Not evidence that production data was already corrupted. |
| R01 | Medium / Confirmed code gap | `app/services/password_policy.py:140–143` calls unrestricted `read()` on HIBP. A mocked response recorded size `-1`. `neorain_google_revision_probe.py:25–29,51` checks `response.content` after requests/gspread already buffers it; `neorain_google_runtime.py:82–89` calls unrestricted JSON decoding. | Provider malfunction/oversize can still consume excessive worker memory outside the repaired flight/weather adapters. Reuse bounded pre-buffer reads with realistic text/JSON caps, content-encoding handling and controlled provider errors. Fixed trusted destinations lower direct exploitability; no malicious provider response was tested in production. | Low–medium | Blocks unconditional PASS; not an established active exploit or immediate operational shutdown requirement. |
| R02 | Medium / Confirmed lifecycle gap; sustained leak unproven | `neorain_google_runtime.py:59–69`, `neorain/certification_routes.py:26`, `neorain_google_revision_probe.py:42–79`: owned writer/metadata clients have no explicit close/finally. A mocked metadata-client construction failure left the already-created writer's session close count at zero. | SDK resources may survive until GC; partial-construction cleanup is missing. Add explicit owned scopes/finally, close responses, preserve borrowed ownership and do not certify a writer. Measure retention separately before calling it a leak. | Low–medium | Blocks unconditional lifecycle sign-off; rare administrator/pre-deploy path limits ordinary traffic exposure. |
| R03 | Medium / Confirmed test reliability gap; application impact varies | Broad Python and browser results below, with exact failure IDs/classifications in supporting evidence. Prior curated green groups were not a complete baseline. | Remaining failures reduce confidence in full-system changes. Reproduce each failing group independently, separate fixture/clock/assertion/interaction issues from app defects, fix only after contract verification. Do not skip or relax security tests. | Medium; depends on failure | Blocks unconditional PASS. Any confirmed aircraft/permission/integrity regression must be treated as a release blocker. |
| R04 | Medium / Needs verification | Render memory rose across the observed old-instance and new-instance windows. The new release has about one hour of coarse samples, including one natural capture. Neon endpoint settings and one connection snapshot were readable, but CU/suspension time series, SQL-frequency statistics, cgroup/PSS and latency histories were unavailable. | Sustained resource/cost guarantees remain unproven. Observe representative normal operation and naturally scheduled captures with bounded diagnostics; retain all guards and low-priority deferral. No stress test, restart masking or plan increase recommended. | Low measurement effort; root-cause work unknown | Limits reliability/cost sign-off, not a proven leak. |
| R05 | Low / Confirmed optimization opportunity | `neoscorpion_dispatch_fragments.py:29–46` renders each row before comparing its HTML hash; at most 80 mission hashes are round-tripped. | Wire savings do not imply equivalent render/SQL savings; beyond 80 rows unchanged rows are retransmitted. Profile representative populations before a narrowly scoped canonical-fingerprint improvement. Never sacrifice permissions, CSRF, drafts or identity. | Medium | Nonblocking. |
| R06 | Low / Confirmed limitation | Live FROST has zero matched subsequent treatment/physical outcomes in the inspected correlation population; two of 24 recent slots were unavailable for valid reasons. | Collect independent outcome evidence through normal Xchange/optional inspection workflows. No missing-flight negatives, operational warnings, retraining or model promotion. Keep these as research-only scores. | Ongoing evidence collection | Blocks physical-frost/model approval, which this release does not authorize. |
| R08 | Low / Confirmed counts; optimization needs validation | `alp_import.py:139–157` flushes each tail through `sort_date_operations.py:202`; `neorain_overrides.py:51–72` performs protected canonical/override reads on each flush. Synthetic 6/18 imports produced 25/73 SELECTs, including 7/19 mission SELECTs. | Additional N+1 work remains outside the repaired leadership path. Consider batching legitimate tail/authority discovery and flushes only after measuring real import frequency; never remove the row locks or override checks to satisfy a budget. | Medium | Optional optimization; no measured production CU savings or correctness failure established here. |

Recommended order: first correct R07 in a separately authorized operational
authority package and verify its canonical-writer/concurrency tests; address
R01/R02 in a separately authorized small transport
package; triage the exact R03 failures before another broad behavior change;
complete R04 observation during ordinary use; profile R05 only if measured
cost warrants it. Continue R06 collection without mandatory nightly entries.
No automatic implementation prompt, permissions reset or historical repair is
part of this report.

## Limits and final sign-off

- Automated groups ran on Windows/Python 3.13.14 and local PostgreSQL 17.11,
  whereas production is Linux/Python 3.14.3/PostgreSQL 18. This is not a full
  production-platform CI run.
- Isolated fixtures cannot certify every production role/data combination or
  concurrent workload. No destructive/stress/penetration tests ran in production.
- Neon settings and limited activity metadata were read, but no usable SQL
  frequency statistics, suspension time series, full cgroup/process metrics or
  populated latency series were available. Savings in CU-hours and production
  bandwidth were not invented from isolated benchmarks.
- Google permission/write-certification and outside-provider availability were
  mocked for functional tests; production Google records were not changed.
- Historical SPEAR/SubZero objects were not exhaustively reread. Corruption,
  retries and legacy receipt classification were tested with synthetic objects;
  only the two selected FROST captures received auditor-initiated read-backs.
- Existing deprecation/legacy ORM/fixture warnings remain visible. Browser
  reruns are explicitly disclosed and do not establish long-term flake freedom.
- Test clusters contained synthetic records only. Resource configuration changes
  applied only to disposable local PostgreSQL, never Render/Neon. Initial local
  setup failures are recorded separately from clean application results.

**FAIL** — R07 is a confirmed material operational-integrity defect and the
remaining broad regression failures have not been fully classified or resolved.
The verified remediations and healthy production responses do not justify final
sign-off. This decision is not a request to disable services or change Google
configuration without authorization. No production corruption, exploitation,
CU savings, sustained memory plateau or physical-frost accuracy is inferred.
Application code and production operational state were not modified by this
verification task.
