# FROST prediction reliability and learning audit

**Release update:** The owner subsequently authorized deployment and further measured memory fixes. The original read-only findings below remain dated evidence; the final release addendum at the end supersedes the earlier local-only correction description.

Date: November 18, 2026. Starting source and inspected LIVE release: `bafaeaea52e37d53daedb795e2467adc15baebd6` (`dep-dbv0tgh42hec73ari9k0`). Production investigation preceded local edits. **Local corrections only: no commit, push, deployment, model fitting, promotion, or production record changes.**

## Assessment

The eight frozen historical models are intact and calculate real H0/H1/H3/H6 research scores. Their exact training dataset contains historical uploaded Xchange evidence, with no NeoSubZero operational observations. No unauthorized training data was found in the registered frozen model lineage examined.

Hourly reliability is **not yet satisfactory**. RFD scored all eight models in 6/8 slots on November 16–17 and 4/8 on November 17–18. Memory admission correctly refuses work when the shared 512 MiB container lacks headroom. Repeated operational contention also consumes acquisition opportunities. The guards must remain intact; the local changes below do not claim to solve sustained memory pressure or guarantee 8/8 nightly coverage.

Three independently reproduced defects/waste sources have local corrections:

1. Empty Xchange/departure reconciliation wakes wrote shared worker leases and, for departures, unchanged cursors despite having no work.
2. The final uncommitted hourly retry could remain visibly PENDING after its acquisition window closed. Status now projects it as MISSED without mutating evidence or performing historical acquisition.
3. Archive diagnostics counted expected HEAD 404 existence probes as failures without distinguishing them from actual HTTP transfer errors. Additive counters distinguish these; retry and verification behavior is unchanged.

Only RFD exists as an active production gateway in the inventory. KRFD weather, model contracts, and source-time rules are RFD-specific. Other gateway uploads retain gateway isolation; enabling KRFD models elsewhere would require separately verified station/model compatibility, not a gateway-code substitution.

## Production evidence and actual coverage

Read-only Neon transactions explicitly began with `SET TRANSACTION READ ONLY`. Render logs/metrics and one authenticated archive download were read. No capture, upload, control-save, training, correction, or operational mutation endpoint was invoked.

RFD database settings enable Monday–Thursday night windows, 21:00–05:00 America/Chicago. The eight eligible hourly slots below are 03:00–10:00 UTC in November standard time. Acquisition ends at 11:00 UTC. Existing scheduling uses database windows, operating days and timezone, including distinct UTC slots at daylight-saving transitions; no schedule was hardcoded or changed.

| UTC slot | Nov 17 (Nov 16 night) | Nov 18 (Nov 17 night) |
|---|---|---|
| 03:00 | 8/8 | 8/8 |
| 04:00 | 8/8 | 8/8 |
| 05:00 | 8/8 | 8/8, 2 deferrals |
| 06:00 | MISSED, 16 deferrals | MISSED, 57 deferrals |
| 07:00 | 8/8, 2 deferrals | MISSED, 50 deferrals |
| 08:00 | 8/8, 14 deferrals | MISSED, 12 deferrals |
| 09:00 | 8/8, 23 deferrals | 8/8, 3 deferrals |
| 10:00 | MISSED, 58 deferrals | Expired uncommitted RETRY, 57 deferrals |
| Total | **48/64 model evaluations; 6/8 slots (75%)** | **32/64 model evaluations; 4/8 slots (50%)** |

No models scored in the missed slots. The expired RETRY is counted as missed in this audit, not as a future opportunity or a captured prediction. All ten successful slots have durable 8/8 scoring and archive-verification receipts, with no source errors. These nights are observations, not a controlled before/after experiment; multiple releases occurred during the later night.

Across the retained capture index: 209 READY captures comprise 132 with 8/8 scores and 77 with 0/8; 28 rows are MISSED and one old RETRY is expired. The 77 unavailable captures span October 28–November 5, including earlier policies and hours that are not all eligible under today's schedule. Do not use this historical row total as a current schedule coverage denominator. They contain 1,056 scored and 616 unavailable model results. No `auto_gap` receipts were present.

### Numerical verification

Authenticated GET `/neosubzero/frost-vault/auto-evidence/3764` returned the hash-verified November 18 09:00 UTC archive. Actual cutoff: 09:15:47.694069 UTC; durable commit: 09:15:48.881409 UTC. All scores were replayed locally from its exact 44-element feature vectors and unchanged frozen artifacts. All eight replay errors were **0.0**. No weather reacquisition or fitting was involved.

| Horizon | Logistic score | Forest score |
|---|---:|---:|
| H0 | 0.08645916245959598 | 0.21928453630559167 |
| H1 | 0.35529566751406494 | 0.14559399434561146 |
| H3 | 0.05252859549805268 | 0.14530397182354163 |
| H6 | 0.0003391307645042468 | 0.08946617912603072 |

These predict `frost_reason_among_recorded_departure_treatments`, **not physical-frost probability**. Thresholds remain 0.5. Missing gust, observed visibility/freezing-fog fields, aircraft exposure and prior pretreatment remain explicit missing values; core weather was available in this sample. No guessed parking, surface temperature, heuristic risk substitution or operational frost alert was introduced.

### Resource and archive causes

At 09:05:02 UTC, a deferred capture took 0.898 seconds and reduced worker RSS by 172,032 bytes; container usage was 430,559,232 bytes. Of that, anonymous memory was 387,072,000 bytes, file memory 19,472,384 and kernel memory 23,916,544. This is principally anonymous process memory, not merely removable download file cache. The worker RSS was 390,230,016 bytes. A nearby sample reached 431,448,064 container bytes.

After worker replacement, the successful 09:15 capture took 41.249 seconds. Worker RSS increased by 64,229,376 bytes, and the ending container sample was 360,865,792 bytes. These logs demonstrate the admission constraint and the cost of a successful capture; they do not prove which unrelated request allocated earlier retained memory or establish a FROST memory leak. Render's coarser memory samples ranged up to 424,747,000 bytes during this window; sampled usage is not a peak-allocation guarantee.

The outer guard retains 75% usage and 128 MiB headroom. HRRR additionally retains its 32 MiB reserve, giving a 352 MiB admission ceiling on the 512 MiB container. Operational requests always take priority, with the existing bounded cooperative wait. Shared PostgreSQL leases serialize work and recover expired owners; no evidence of an indefinitely held worker lease was found. The worker receipt was READY, with 23,724 historical claims at 22:30 UTC; this cumulative count is not a per-Sort query or cost measurement.

FROST archive diagnostics, complete paginated Render sample **November 18 03:00–11:00 UTC**, deduplicated by log ID:

| Measurement | Actual observed value |
|---|---:|
| Aggregate lines | 58 |
| HTTP attempts | 584 |
| PUT / GET / HEAD | 102 / 283 / 199 |
| Declared outbound body bytes | 19,953,730 |
| Declared inbound body bytes | 23,383,914 |
| Legacy HTTP-error counter | 102 |
| Unknown body lengths | 0 |

`put_bytes` deliberately probes immutable objects with HEAD and uploads absent objects, then verifies bytes through GET; callers verify the JSON content hash as well. A typical successful new snapshot reports four calls (HEAD, PUT, GET, GET), one legacy “failure,” and a verified receipt. The 102 counter increments therefore **cannot be called 102 failed archives**. The old counter lacks operation/status attribution to conclusively classify every historical increment. All four successfully scored slots in this night have verified archive receipts; no auto-capture archive retry remained in the inspected inventory. No source hash corruption was observed. The local additive counters make future classification explicit without hiding PUT/GET errors or changing durability.

Successful HRRR acquisition receipts total 19,012,260 incoming provider-body bytes across the four scored slots. Partial acquisitions aborted before a receipt commits are not completely attributed by that total. Incoming provider bytes are not Render outbound bytes. Archive counters omit headers, TLS and other network overhead and are not a billed-byte meter.

Render bandwidth API data is service-wide, not FROST-specific. Neon reports project-wide November consumption: 1,373,346 active seconds, 347,304 compute seconds and 76,909,607,961 transfer bytes at the read. These are cumulative project/endpoint consumption fields, not eight-hour FROST usage. No attributable FROST CU-hours, dollar cost or production savings can be honestly derived from them.

## Training provenance

The 11 production documents are original manually imported Xchange CSV exports, archived October 24, covering October 30, 2015–June 25, 2026. Their SHA-256s and row counts match all 11 preserved local originals. The source catalog has **36,614 distinct rows**, zero overlaps and zero unresolved revision conflicts.

Verified lineage:

- Source catalog: `7a83f7cd7767a11ba7db052c615c1ae02bf2432125a0344d00d73e115f30614f`.
- Corrected historical source manifest: `085065db22aeb34e1b78379fc85317a8c3b40e89a56df174723b4d39feccde9a`.
- Exact frozen native training dataset: `c5760b5e99389eeb5fec79105b755047e1fd6215632cba3f96437ea728ef7b61`, retained at `instance/frost-local/phase11/datasets/<hash>.json`. Its canonical sealed hash verifies.
- Frozen baseline: `b8f33680a91ae65b650b69b2c0872ed024356d3c741651e2e52d4a62bbafb783`, frozen October 28 at 19:18:23.022502 UTC.
- Dataset: 1,117 horizon examples from the 2020–21 through 2025–26 historical seasons, source documents 6–11. H0/H1/H3 each contain 280 nights (83 FROST-reason, 197 other-treatment); H6 has 277 (83/194). These are horizon examples, not 1,117 independent nights.
- `operational_observations=[]`; confirmed physical positives and credible physical negatives are zero; physical-frost training readiness is false. Every example's document ID/hash matches the historical source catalog. No 2026–27 nightly testing example appears.

All artifact byte hashes, sizes, inner fingerprints, horizon contracts, four original report hashes and baseline identities verify. All 32 retained numerical parity checks pass. Production's eight registered identities match these exact hashes:

| Model | SHA-256 |
|---|---|
| H0 logistic | `386006d811e2a8651284f55e22e306dc1a28576ff3de8797b862dd2cffd43e79` |
| H0 forest | `f83dbef382ee922d86fe915999ca251f40b1d89de64649bfc37a9f77e985b3bf` |
| H1 logistic | `a69b01875bbaa9661755a52daab5ccbfce063278b44d0a5cd7792423813fdb45` |
| H1 forest | `de9b55eced33377f22ca353f14d3d4e9454904e5f76334ea6d665dd9137bdf12` |
| H3 logistic | `3187e4770df84ff0a16fcfcc9c861a0feee94d004168fc79e54d324f56619175` |
| H3 forest | `56914ebd04186d14c60689b2a654d7f6ae4ecdd88d5395c098a313578f6adeb5` |
| H6 logistic | `b0bb1c98520da0e5f33e751bec93b0dccde3e99aedd848e6a3bab8739450fe84` |
| H6 forest | `8647c7b9cce61e5a72e1669668c30ee8d1e6f2171e99352fbf3e305f4047b611` |

There are also two older October 27 research registry artifacts, H0 and H1, whose recorded datasets are `dce4665eeb13bbaf278c84e22be518f29f00d989aa24dabf0be6e5e0e371c1f2` and `a95e9754e7d705213dc47d9e318660a328828a43ebeb48fcef73682c0a93a39e`. They precede the native frozen baseline and use the historical upload catalog. There are **no model releases**. This audit verifies the eight native artifact bytes and their complete local lineage; it does not claim to inventory arbitrary unregistered workstation experiments.

## Uploads, learning and evaluation contract

No production Intelligence control row or enablement audit exists: all three switches remain default OFF. Research capture remains independent. Existing platform-Grandmaster, scoped authority, verified-email, CSRF, version and transaction guards remain unchanged and were tested.

| Source/control | Verified behavior |
|---|---|
| Uploaded Cryotech/Xchange CSV | Authenticated staging, gateway validation, encrypted original, immutable archive/read-back, actual fluid/tail/reason/time extraction and source hashes. Eligibility is independent of live-learning and retraining switches. |
| Historical dataset ingestion | Existing local refresh/assembly reads the verified source catalog and joins eligible uploads to KRFD weather. It checks source identity, actual spray, Central date/time/DST ambiguity, weather quality, source revisions and duplicate evidence. Ingestion/assembly does not call fitting. |
| FROST reason with actual spray | Treatment-reason cohort, likely FROST-reason label; never independently confirmed physical frost. |
| Other documented treatment | Other-treatment cohort; never a physical frost-negative merely because the official reason differs. |
| Pretreatment | Anticipated/preventive cohort, confounded for physical-outcome evaluation; not a verified presence/absence example. |
| Missing/ambiguous identity, time, actual spray or quality | Review/exclusion/UNKNOWN, with source evidence retained; never fabricated data. |
| Prospective comparison | Only genuinely committed pre-event scores in the fixed horizon/target window may match. Late commits do not become historical predictions. Revisions retain source ownership and conflicts require review. |
| Historical comparison | Retrospective/season-isolated backtest, explicitly distinct from prospective and physical-frost validation. |
| Live learning OFF | Current/pre-launch nightly spray/no-spray records cannot become labels. Prior testing lacks an admissible enablement epoch; switching ON later does not admit those old records. |
| Live learning ON | Only eligible future, explicit, verified physical inspection evidence after enablement and new-Sort creation may be considered; this is not automatic training or scientific validation. |
| Retraining OFF | Uploads still ingest and evaluate; no automatic fitting/publication. Manual workstation training commands remain separate. No fitting command was run in this audit. |
| Operational Predictions OFF | Research only; activation remains blocked without independently validated, approved compatible physical-frost models and deliberate confirmation. |

Raw source manifests' `training_eligible=False` means an unvalidated source document itself is not a finished training example. It is not a live-learning-toggle exclusion. The canonical dataset assembler validates individual records before eligibility. This distinction should be preserved, rather than changing every upload's raw flag to true.

Production has no `xchange_page`, `xchange_outcome` or `auto_match` rows. The latest uploaded data ends before prospective captures began. Consequently there are **no uploaded-log prospective correct/miss/false-positive counts to claim today**. The existing offline evaluator records cohort-specific correct/missed/false-alarm metrics, unknown and unavailable predictions, source limitations and confounding. Synthetic tests verify matching and exclusion. Old model cross-validation/backtest statistics are not a completed real-world validation result. The checked-in prospective summary is explicitly a dated October 28 local receipt, not a live production coverage counter.

Substantial historical assembly, weather matching and evaluation remain on the workstation, independently of the automatic-retraining switch. Newly uploaded history requires the existing incremental local refresh/assembly/evaluation path to update that dataset; Render intentionally does not fit models or reconstruct a decade of weather. Do not claim a background automatic dataset-refresh service exists.

## Local corrections and measured efficiency

Changed application files:

- `app/services/frost_xchange_links.py`: bounded due-page read before taking the shared worker lease; fresh due-page lookup and canonical claim still occur after the lease.
- `app/services/frost_departures.py`: read-only gateway-scoped work hints before leasing; preserve bounded discovery, archive recovery and late-outcome reconciliation; stop rewriting unchanged discovery/outcome cursors. New work arriving after an empty preflight is handled on the existing next wake.
- `app/services/frost_auto.py`: expired uncommitted slot status/count projection, including the final Sort hour, within the existing aggregate query. Live leases, committed staging and archive retries are protected. GET remains read-only; no extra status query, acquisition, worker or timer.
- `app/services/archive_transfer_diagnostics.py`: retain legacy `failures` and add `expected_head_misses` / `transfer_failures`, with no request bodies, keys, URLs or credentials logged. Transport errors, retries and byte verification are unchanged.

Equivalent warmed **synthetic SQLite empty-work** comparison against the original HEAD functions, using the same gateway/cursor/workload:

| Drain | SELECT before → after | UPDATE before → after | Commit calls before → after | Local elapsed ms before → after |
|---|---:|---:|---:|---:|
| Xchange pages | 4 → 1 | 2 → 0 | 3 → 0 | 1.808 → 0.690 |
| Departures | 9 → 5 | 2 → 0 | 5 → 0 | 3.330 → 2.228 |

Both now end the read transaction with rollback when empty. No archive calls occur on those empty paths. These single-run local timings demonstrate the eliminated work, not Neon latency or realized CU savings. Pending real work still uses the existing locks and lease; uploads/outcomes are not disabled or delayed by a changed polling cadence. After future uploads exist, bounded late-outcome reconciliation remains intentionally active, so this empty-work saving must not be extrapolated to all wakes.

No memory guard, schedule, training policy, model artifact, ranking, operational status, warning or application workflow was changed. No attempt was made to fix reliability by raising memory thresholds or admitting guessed weather.

## Focused validation

- **93 Python tests passed**, zero skips/failures in the final focused run: new audit contracts, archive diagnostics, Intelligence authority/isolation, cooperative priority, exact eight-model scoring, schedule/DST, committed archive recovery, prospective matching/UNKNOWNs, upload pipeline, departure evidence and local dataset assembly.
- **11 distinct PostgreSQL checks passed** across the isolated-schema audit/policy/process runs: five audit persistence/projection/ingestion contracts, three authority/concurrent-save/rollback tests, one hard-stop lease recovery and two OS-process/timer capture checks. The two process tests passed in the final rerun (69.256 seconds).
- Frozen artifacts: eight byte hashes/contracts/report hashes, 32 parity checks and eight exact archived-vector score replays passed.
- All eight modified/new Python files parsed successfully; `git diff --check` passed. No JavaScript or UI was changed, so no unrelated JS/browser sweep was run. The sole production browser use was an authorized read-only archive GET.

Initial test obstacles were retained as evidence: sandbox restrictions blocked atomic hard links and loopback PostgreSQL; the available numerical package was incompatible with Python 3.12. Compatible official wheels were installed only in an ignored local test folder, with no requirements/deployment changes. Reruns used authorized fixture access. No warnings were suppressed.

Two existing test fixtures also required legitimate repairs: `tests/test_frost_phase15.py` lacked the now-required configured Sort window; `tests/test_frost_auto_process.py` had a Windows-spawn circular import and lacked that same schedule. They now initialize the normal route import order and create explicit synthetic database schedules. Assertions, real process execution, authority guards and timing protections were preserved. New regressions live in `tests/test_frost_learning_reliability_audit.py` and `tests/test_frost_learning_reliability_postgres.py`.

Local evidence directory: `instance/frost-reliability-learning-audit-20261118/`, including final test logs, `frozen-provenance.json`, `provenance.json`, `score-replay.json`, and `steady-sql-comparison.json`. Initial unsuccessful test logs remain present. Existing synthetic databases, unrelated files and jobs were preserved; no directory cleanup was performed.

## Proposed next actions and limits

1. **Approve/review the small local correction set before release.** It removes measured idle writes and misleading failure/pending accounting. It is not a claim that scoring reliability is fixed in production.
2. **Investigate retained shared-worker anonymous memory using existing bounded diagnostics during an equivalent busy Sort.** Correlate operation-level RSS/PSS/cgroup components with the first growth and later deferrals. The measured 387 MB anonymous baseline needs attention; changing guards or blaming file cache is unjustified. No additional worker or hosting upgrade is proposed.
3. An unused HRRR cloud-ceiling HGT field is still acquired even though the frozen feature contract excludes its unresolved datum. The inspected successful source had a 421,721-byte HGT tile. Omitting it could reduce decoding/transfer, but current immutable point replay expects that source set. **Not changed**: require explicit backwards-compatible replay/provenance tests before an optimization, and do not claim it solves pre-acquisition memory deferrals.
4. Use the existing incremental workstation ingestion/evaluation path after the next verified upload. Record prospective outcomes only where genuine pre-event captures exist; retain retrospective labels otherwise. This does not require enabling live learning, automatic retraining or operational alerts. Do not refit or replace the frozen models without owner approval.

No complete post-fix production night exists because this package was not deployed. No sustained-reliability, production cost-saving, physical-frost accuracy or independent-validation claim is made. Production remains unchanged pending review.

## Source register

- `app/services/frost_auto.py:242–386,388–486`: acquisition/lease/retry/scoring lifecycle and read-only status.
- `app/services/frost_schedule.py:18–87`: configured windows, weekdays and timezone.
- `app/services/frost_resources.py:32–139`: bounded cooperative operational priority.
- `app/services/frost_hrrr_point.py:30–108,122–258`: memory reserve, bounded source reader, decoder and availability proof.
- `app/services/frost_vault.py:129–181`: immutable object existence checks and read-back/hash verification.
- `app/services/archive_transfer_diagnostics.py:18–108`: bounded aggregate accounting.
- `app/services/frost_documents.py:20–105`: source staging, original integrity and upload delivery.
- `app/services/frost_xchange_links.py:16–149`: source-page staging, canonical matching, late-revision rules and bounded drain.
- `app/services/frost_departures.py:33–91,143–252`: authoritative physical identity, correlation, discovery and delivery.
- `app/services/frost_analysis.py:35–88,200–233`: classification, actual-spray/source/time evidence and weather matching.
- `app/services/frost_intelligence.py:18–128`: authoritative platform permissions, OFF defaults, immutable control auditing and capture-time eligibility.
- `app/services/frost_dataset_contract.py:34–87`: sealed source/hash/quality contract.
- `scripts/frost_dataset.py:18–144`, `scripts/frost_local.py:113–190`: incremental source/weather assembly and separate training entry points.
- `scripts/frost_prospective.py:101–120,255–297,376–413,447–567`: frozen lineage, isolated source cohorts, pre-event matching and evaluation.
- `scripts/frost_forecast_native.py:103–193,241–279`: historical native features and separate fitting; no training invoked by this audit.

## Authorized reliability release — November 18

Latest `origin/main` was fetched and confirmed unchanged at `bafaeaea52e37d53daedb795e2467adc15baebd6`. The uncommitted audited changes were inspected, refined and verified for the owner's requested release. Only FROST code, focused tests and this report are included. The exact deployed commit and AutoDeploy ID are recorded in the final delivery response and local deployment receipt; this addendum is written before the single release commit.

### Changes approved for release

- Empty Xchange and departure workers now return without lease/cursor writes. Completed or not-yet-due capture slots also return before claiming a worker. Fresh checks under existing leases remain authoritative; no new cache, timer, background service or polling interval was introduced.
- Expired uncommitted retries are reported as MISSED through a read-only projection. Committed archive retries and live leases remain protected. No old prediction is reconstructed.
- Expected HEAD 404 probes now increment `expected_head_misses`, **not `failures` or `transfer_failures`**. Raw HTTP status accounting is retained separately as `http_error_responses`. This supersedes the audit-stage implementation that retained the old ambiguous `failures` meaning. PUT/GET failures remain failures. No durability check, retry or exception outcome was removed.
- HRRR's stricter admission check runs before METAR/NWS downloads. A memory-ineligible attempt now stops before weather transfer and SDK construction, instead of downloading weather only to fail HRRR admission. Existing mid-transfer, decoder, scoring, window and operational-priority guards remain.
- Acquisition, all eight model reads, staging and snapshot delivery share **one lazy context-local archive client**, closed and reclaimed on every exit. No client/credential cache crosses jobs, users or gateways. Empty work constructs no client. Temporary source/score/snapshot references are released after durable staging, before decoding the delivery copy.
- At existing collection/admission points, an idle Linux worker can return already-freed allocator pages using `malloc_trim(0)` after GC. This frees no live allocation, changes no allocator configuration or admission limit, and safely falls back to GC on unsupported platforms. Trimming is skipped during foreground requests or recovery/CPU pressure. [Linux's allocator reference](https://man7.org/linux/man-pages/man3/malloc_trim.3.html) documents the unused-page behavior and thread safety. The exact 75% / 128 MiB guard and extra 32 MiB HRRR reserve are unchanged and rechecked after reclamation.
- New HRRR captures omit the HGT cloud-ceiling field, already excluded from the frozen 44-feature contract because its datum is unresolved. The immutable point explicitly declares this single fixed exclusion. The local verifier replays both old complete points and new declared-exclusion points; arbitrary exclusions are rejected. All model input values, missingness and scores remain unchanged. Existing archives and model files are untouched.
- Existing opt-in, rate-limited memory diagnostics now distinguish weather acquisition, HRRR decoding/acquisition, frozen scoring, archive delivery and idle reclamation. They record numeric memory/timing evidence, not operational payloads or credentials. No new diagnostic service or database table was added.

### Measured results and limits

All measurements below are **local controlled results**, not realized Render/Neon billing savings.

| Equivalent workload | Before | After |
|---|---:|---:|
| Warm empty Xchange drain | 4 SELECT / 2 UPDATE / 3 commits | 1 SELECT / 0 writes / 0 commits |
| Warm empty departure drain | 9 SELECT / 2 UPDATE / 5 commits | 5 SELECT / 0 writes / 0 commits |
| Completed-hour capture wake | 12 SELECT / 3 UPDATE | 4 SELECT / 0 writes |
| Fresh-process Linux archive ownership | 2 SDK clients; peak RSS 152,743,936 bytes | 1 SDK client; peak RSS 147,697,664 bytes |
| Linux idle reclamation in a repeated one-client probe | RSS 147,873,792 bytes after GC | RSS 135,528,448 bytes after reclaim |
| Cached actual HRRR source replay | 67 reads; 4,957,314 source bytes | 62 reads; 4,576,271 source bytes |

The client comparison reduced observed peak RSS by **5,046,272 bytes (4.81 MiB)**. A separate post-GC reclamation observation returned **12,345,344 bytes (11.77 MiB)** in **29.596 ms including collection**. These are separate experiments; they must not be summed into a production saving. The HRRR replay removes five reads and **381,043 bytes (7.69%)** from that cached October 28 cycle. The audited November 18 cycle had a 421,721-byte HGT tile alone, demonstrating size variation. Depending on content reuse, two distinct HGT source objects and their verification traffic can also be avoided.

The cached decoder's Python allocation peak was 1,460,613 → 1,457,577 bytes, essentially unchanged; fresh-process RSS was noisy/slightly higher in the second run. Do not claim a decoder-memory improvement. Large cold/warm filesystem timing differences in the WSL probe are not an HTTP latency improvement. A direct-botocore-client alternative was measured but not adopted; the shared existing provider implementation remains unchanged.

The Linux experiments use the preserved Ubuntu/Python 3.12 environment and real boto3 object construction, with network-mocked archive storage. Render runs Python 3.14. The HRRR comparison reads preserved, hash-checked real source bytes without downloading new weather. The helper returned real freed pages in Linux, but the amount available to return in a busy production worker is workload-dependent. No sustained production memory saving, CU reduction or dollar saving is claimed before a comparable operational Sort.

### Validation and preserved rules

- **137 focused Python tests passed**, zero failures/skips in the final run: memory/idle paths, HEAD accounting, full frozen-model scoring, old/new source proofs, missing fields, deferred acquisition, window/DST behavior, committed archive recovery, uploads, source isolation, dataset assembly, permission/CSRF checks and controls.
- **17 isolated PostgreSQL tests passed**, including the existing audited fixes plus six new memory/ownership tests, protected-control races, revocation, rollback, hard-stop recovery, real-process duplicate capture and timer behavior. The bounded test set took 466.712 seconds; no full-system suite ran.
- Additional final memory/diagnostic checks: 23 passed. These overlap the full focused run and are not added to its total.
- The eight frozen artifact/report hashes and contracts verify. Old/new HGT source paths produce equal features/missingness and scores for every H0/H1/H3/H6 model pair. Arbitrary source exclusions, corrupt source bytes, future availability and stale ownership still reject.
- The existing fixture repairs remain explicit configured synthetic Sort schedules and Windows-spawn import initialization, not weakened assertions. The downstream HRRR-deferral fixture now explicitly passes initial admission before testing a later deferral. No broad skips, warning suppression or expected-failure markers were added.
- Syntax and whitespace checks passed. No JavaScript or UI code changed. No migration, model fitting, model promotion, production test operation, historical rewrite or cleanup ran. Uploaded-log eligibility remains independent of the switches. Live Sort learning, automatic retraining and operational predictions/alerts remain OFF; production had no enablement record at the release precheck.

Evidence: `instance/frost-reliability-release-20261118/` contains final focused/PostgreSQL logs, memory/decoder probes, final admission/diagnostic checks and completed-hour SQL comparison. The audit's prior evidence directory and every unrelated file/database/job remain preserved.

### Production acceptance and follow-up

The last completed-night coverage remains **6/8 and 4/8 hourly slots**. At release preparation it is outside the configured night window; no post-release eligible capture has occurred. Deployment verification must not manufacture one to produce a favorable metric.

Verify the exact commit LIVE, bootstrap/worker startup, health/login, authenticated FROST status/controls, unchanged model hashes and OFF controls. Read-only status must display the old final-hour retry as MISSED while preserving its stored historical row. Inspect rollout errors and memory diagnostics. The following night's existing scheduled captures should supply genuine acquisition/scoring/archive/reclamation measurements; retain every slot's actual 0/8 or 8/8 result and deferral reason. A restart's low initial RSS is not proof of sustained busy-Sort reliability.

Remaining limitation: other NeoApps operations can retain substantial shared-worker anonymous memory. The release reduces measured FROST allocation/transfer churn and can reclaim free pages, but cannot reclaim live objects or guarantee eight successful hours when operational work occupies the protected budget. Keep the guards and foreground priority intact; use the new bounded phase evidence to assess real overnight improvement before any further changes.
