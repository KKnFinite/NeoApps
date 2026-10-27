# NeoSubZero Release 4 — FROST Vault

## Boundary and storage contract

FROST is a private logical vault on the existing R2 provider, not a new provider,
subscription, public file server, prediction model, or operational spray database.
SPEAR fueling objects and exports are unchanged. New SubZero evidence uses
`frost-vault/v1/gateway-{id}/` with categories:

- `original-xchange`, `extracted-xchange`, `manifests`
- `spray-history`, `treatment-context`, `weather`
- `frost-observations`, `evaluations`, `training-datasets`

The latter categories reserve explicit versioned boundaries; this release does
not invent observations, evaluations, or training datasets. Operational spray
snapshots already carry their recorded weather, original tail/mission context,
participants and correction provenance.

Legacy `subzero-spray/v1/{gateway}/` and
`neofrost-treatment-context/v1/{gateway}/` objects remain in place. Existing
successful receipts are not blindly re-exported. The authenticated catalog reads
legacy and new objects with gateway and content-hash verification. No object is
retired, renamed, or deleted; source IDs, revisions and previous keys survive.

## Source ingestion

Settings links to `/neosubzero/frost-vault`. Import and retrieval reuse
MotherBrain's `neosubzero.settings.edit` rule and current gateway authority.
IMPORT CSV is a touch-sized real HTML file input on desktop and mobile, using
the native Files picker rather than drag-and-drop or extension automation.
Selection uploads immediately; transfer progress, processing and failure/retry
feedback preserve the selected file on errors. Seasons are separate documents.
Original and extracted downloads are authenticated attachments, never public
object URLs. Uploads use existing CSRF enforcement. Paths, binary input,
malformed CSV, oversized files and wrong-gateway exports are rejected.

Supported input is the actual supplied UTF-8 Xchange deicing-log CSV schema.
The limit is 10 MiB / 25,000 records. Other CSV schemas retain their original
with an explicit unsupported status. PDF, XLSX and imagined export formats are
not claimed as supported.

The additive catalog records content SHA-256, original filename/length, importer,
actual import UTC time, source date range, parser version, extraction/review
status and immutable import audit. Gateway/hash uniqueness prevents concurrent
duplicate imports. Source corrections require a new content hash; originals are
not replaced. Duplicate attempts append audit entries without creating new
documents or operational transactions.

Original bytes are encrypted in durable database staging before provider I/O.
The worker writes an encrypted original, extracted JSON and provenance manifest,
then reads and verifies them before setting OFF-SITE VERIFIED and releasing the
staged blob. Partial writes are idempotently reconciled; outages retain staging.
No cloud I/O blocks spray, FIN or CLEAR.

Encryption uses a domain-separated key derived from the existing application
SECRET_KEY, with SECRET_KEY_FALLBACKS for rotation. Operators MUST retain these
keys/backups while encrypted evidence needs them; removing every applicable key
makes the originals unrecoverable. Use the existing private provider security
and backups. FROST evidence has no automatic expiry or public sharing.

Retries reuse the existing bounded, throttled, demand-driven archive worker.
SubZero/admin traffic, including normal catalog status refresh, wakes it; this
is not a new always-running queue. With no requests, retries wait for subsequent
traffic. Delivery errors remain visible to administrators. The first-import
manifest is immutable; subsequent duplicate-attempt audits remain in the
database audit catalog. Spray correction revisions retain the existing archive
reconciliation contract.

## Extraction quality and the supplied 2025–26 season

Parser: `xchange-deicing-log/v1`. Every source field and logical record order is
preserved, with source document/hash/record provenance. Type I and Type IV are
separate recorded contributions; missing quantities are not changed to zero.
An export row is not assumed to establish a shared aircraft pass identity.
Source Operator is not silently relabeled Flyer. Missing flight/surface fields
remain missing. ApplicationDate and local clocks remain separate because their
calendar-versus-operational-date relationship is not independently established.
Created/Modified UTC values are never substituted for spray timestamps.

The existing offline `neosubzero_frost_history` adapter is not used for this raw
import: its single-fluid row and combined date/local-datetime contract would
lose the supplied export's independent Type I/IV contributions and resolve an
unverified date convention. Its existing offline tooling remains unchanged;
FROST ingestion does not invoke its event grouping or training-label inference.
No second storage pipeline or operational spray model is introduced.

Supplied source verified locally:

- SHA-256: `cb26890bee895c63913ff6eda7a7b146e5496cb6185c850197c66a0c8a7a28cb`
- 1,381,372 original bytes; 3,053 source records.
- Source application dates: 2025-09-04 through 2026-06-25.
- Type I: 150,887.00 GAL; Type IV: 39,790.00 GAL.

The private source is NOT committed to Git. A spray/reason code is not a verified
frost observation. Every imported row remains frost-unknown and training-ineligible.
Ordinary unsprayed CLEAR/NEGATIVE states are not archived as frost-negative labels.

## Validation evidence

The Release 4 PostgreSQL fixture exercises canonical Alpha–Echo/Remote missions,
Pretreat, pad plus three waiting aircraft, two departure passes, mid-operation
truck/team changes, independent END snapshots, FIN/CLEAR without gallons,
negative/unsprayed CLEAR, late Driver gallons after reassignment, audited
post-CLEAR correction, tail attribution, 14 actual-pass archive comparisons and
sort rollover. Concurrent upload requests produce one catalog entry.

SQLite and PostgreSQL tests also cover populated-schema/idempotent bootstrap,
append-only audits, authorization, private downloads, source hashes, duplicate
imports, malformed/unsupported input, outage/partial-write retries and legacy
reads. Browser coverage uses Chromium/WebKit for uploads, failed-save recovery,
CSRF, authorization, private downloads, status refresh, and 320–1440px overflow;
existing Operations, Deicer/reporting and DUCC suites protect the operational UI.

The unchanged starting baseline had two unrelated NeoStaffing attendance errors:
`test_manual_callout_removal_does_not_restore_when_here` and
`test_not_here_attendance_suppresses_then_restores_selected_callout`.
They are not new FROST regressions. The Release 2 browser helper was repaired to
respect an already-expanded team section preserved by live reconciliation.

No production spray, staffing, inventory or mission data is used as test data.
# Phase 5 historical research contract

Phase 5 uses the existing FROST/R2 namespace, source catalog and archive worker.
No imported row creates an operational spray transaction, no SPEAR evidence is
moved, and no live prediction model is trained or deployed.

The owner confirmed on October 26, 2026 that ApplicationDate is the actual local
calendar date and START/END clocks are Central local time across all seasons.
`frost_analysis.TIME_CONTRACT` records this provenance. America/Chicago supplies
historical offsets; ambiguous fall-back and nonexistent spring-forward times
remain unmatched. Created/modified UTC values are never spray timestamps.
An END clock earlier than START crosses into the next local calendar date.

## Identity and incremental processing

Verified source documents are discovered automatically, including those already
archived before this release. Xchange RecordID is scoped to gateway. Equal IDs
and raw-row hashes are overlapping exports, not new applications. Different
hashes preserve source document/row and revision evidence. A unique latest
LastModifiedUTC determines an explicit source revision; absent/tied modification
evidence requires review rather than import-order overwrite. Missing IDs do not
cause guessed tail/date merges. Original bytes and previous derived objects
remain immutable, content-addressed and retrievable through provenance.

`frost_history_tasks` holds only processing metadata, leases, monthly source and
result pointers, and selected aggregate features. Full source/weather/research
records remain in FROST, not duplicated wholesale into Neon. Both canonical
PostgreSQL and SQLite bootstrap register and verify the additive table.

The existing archive worker is woken every 60 seconds from `run.py`, including
when no browser is open. Work is bounded, transactionally leased and retried
with backoff; remote I/O does not hold operational locks. Weather raw-delivery
checkpoints avoid downloading again after a derived-object delivery failure.
New source hashes invalidate affected monthly partitions, not historical raw
evidence. Increment the analysis or weather version for rule reprocessing;
old versions remain separate. No browser or upload request waits for analysis.

## Weather source and quality

Actual RFD/KRFD routine and special METAR observations come from the
[Iowa Environmental Mesonet archive](https://mesonet.agron.iastate.edu/request/download.phtml),
not current forecasts. The [station catalog](https://mesonet.agron.iastate.edu/sites/site.php?network=IL_ASOS&station=RFD)
identifies IL_ASOS RFD at 42.19151, -89.08760, elevation 220 m, America/Chicago,
archive beginning 1951-03-01. IEM's [public-domain terms](https://mesonet.agron.iastate.edu/disclaimer.php)
permit reuse; the observations are as-is, with limited quality control.

Requests use UTC monthly cache partitions, routine=3 and special=4, bounded
responses/timeouts and a process throttle respecting the documented one-second
per-IP limit. HTTP 429/503 are failures with retry/backoff, never successful
empty datasets. Raw provider bytes, request URL, SHA, fetch time, station and
parser version are archived. Coordinates/station and UTC bounds are validated.
Conflicting same-time measurements are retained but excluded from joins.

Normalized units: Celsius temperature/dew point/spread, percent RH, knots wind
and gusts, true degrees direction, statute miles visibility, feet cloud ceiling,
inches precipitation since hourly reset. Trace and missing values remain
distinct. Cumulative METAR precipitation is not summed across special reports.
Calm wind is not classified as a north-wind exposure.

Each application joins only preceding observations (maximum age 90 minutes),
with 12-hour context, 3/6/12-hour cooling rates, coverage, capped observed-interval
freezing/saturation/precipitation/fog proxies and cloud/wind context. Proxy
durations are not continuous measurements; sparse windows remain incomplete.
[NOAA approximate solar geometry](https://gml.noaa.gov/grad/solcalc/solareqns.PDF)
provides night, sunrise/sunset and solar elevation, not measured irradiance.
[NASA ground-icing guidance](https://aircrafticing.grc.nasa.gov/2_2_2_1.html)
supports retaining moisture, cloud, wind and cold-soak context independently.
No aircraft surface temperature, surface wind or historical stand is invented.

## Labels and analysis limits

The established Xchange reason map is reused, not extended by guessed codes.
F/Frost with actual application is LIKELY FROST PRESENT, strong reason evidence,
not physical confirmation. P/preventative treatment is ANTICIPATED FROST RISK.
Explicit other causes stay NON-FROST TREATMENT; ambiguous reasons stay UNKNOWN.
A trustworthy explicit no-treatment observation is only likely negative when
prior pretreatment is known absent. Missing spray, Posted=False, cancellations,
and assumed weekday exposure never supply negative labels. No credible
unsprayed comparison population has been established in these CSVs.

Research JSON retains exact reasons and evidence strength separately from
features. Monthly/seasonal descriptive cohorts count an application/tail/date,
not trucks or fluid contributions as independent aircraft. Missing-field sample
counts and directional sectors remain available. Treatment-selection/reporting
bias, unknown prior treatment, absent surface/aircraft history and observation
gaps prevent claims of causation, frost-negative prevalence or predictive skill.
MotherBrain/DUCC current parking is not historical evidence. The suggested
position-1/5 southerly shelter effect remains an unvalidated hypothesis; no
hardcoded parking adjustment is applied. Phase 6 requires independent backtests
and calibration before a live prediction model.

## Memory safety

Historical work uses one monthly job per batch and one scoped existing-provider
client, explicitly closed on both success and failure. Container cgroup totals
must show at least 128 MiB headroom and no more than 75% use before a batch may
start. An unknown production memory budget fails closed. No plan upgrade is
performed. A database lease serializes batches across gunicorn processes so
concurrent jobs cannot spend the same container headroom. Deferred retry timestamps are respected: normal polling must not
resume an administrator-approved temporary pause. The 33,000-row/58-column
isolated discovery proof measured 60.55 MiB traced allocations; this is not a
claim about total web-container RSS, which includes multiple processes and SDKs.

## Phase 6: workstation training, private artifacts, inference only on Render

### Local historical evidence; bulk publication retired

Render no longer starts historical catalog discovery, weather reconstruction or
feature analysis, even after the old retry pause expires. Existing retry dates,
memory limits and completed results are preserved. This does not stop original
CSV archival delivery, spray history delivery, or live operations.

The former PUBLISH LOCAL RESULT workflow is obsolete. Authenticated compatibility
routes return HTTP 410 and never consume old ZIPs. The administration retirement
action records prior task IDs/status/retry/source/result pointers in existing
document audit records, marks only pending/retry partitions `local_only`, and
preserves retry dates, originals, weather, previous results and local packages.
It does not falsely mark unuploaded rows processed. The live outbox no longer
invokes historical discovery or processing, but retains operational/source
archival delivery. The old verification automation was removed.

`scripts.frost_backfill` remains a local legacy-package reader/writer. Existing
ZIPs are inputs to `frost_local assemble`, not uploads. All 12,797 previously
computed local rows remain reusable without another weather/feature pass.
Earlier archived results can be retrieved with authenticated `refresh`; if the
web service lacks safe headroom, reconstruct only missing local rows from
hash-verified originals. Existing published objects remain untouched.

No model is trained or activated by deployment, CSV import, the history worker,
or a web request. `scripts/frost_local.py` is outside the application import
graph. Its preparation/fitting/backtesting entry points reject Render
environment variables and Flask request contexts before importing training
libraries. Workstation-only dependencies are in
`tests/requirements-frost-local.txt`, not production `requirements.txt`.

The initial supported target is **frost reason among recorded departure
treatments**. F/Frost is the positive class; other explicit departure treatment
reasons are comparison classes, not proof of frost absence. Pretreat, unknown,
ambiguous, and no-spray outcomes are not silently converted to negatives. A
physical-frost probability model is deliberately unsupported without a credible
untreated observation population. Parking/surface-temperature adjustments and
forecast-weather skill remain unvalidated. DUCC's existing preliminary frost
presentation is not replaced or connected to these candidates.

### Repeatable local workflow

Run from the local checkout, never a Render shell. Sign-in uses the application's
normal username/email/password and CSRF-protected session. Passwords are prompted
with `getpass`, held in memory, and never stored in command arguments or files.
Read-only dataset retrieval requires the same gateway-specific MotherBrain
SubZero administration permission as FROST Vault. No Neon write credentials,
public object URLs, or bucket credentials are required.

```powershell
.venv/Scripts/python.exe -m pip install -r tests/requirements-frost-local.txt
.venv/Scripts/python.exe -m scripts.frost_local --local refresh --origin https://neoapps.onrender.com --email YOUR_EMAIL --with-weather
.venv/Scripts/python.exe -m scripts.frost_local --local assemble --catalog instance/frost-local/source-catalog.json --originals instance/frost-local/originals --cache instance/frost-local/backfill/publishable-results --cache instance/frost-local/backfill/prepared-results
.venv/Scripts/python.exe -m scripts.frost_local --local validate
.venv/Scripts/python.exe -m scripts.frost_local --local prepare --lead-hours 1
.venv/Scripts/python.exe -m scripts.frost_local --local backtest --version rfd-candidate-h1-v1 --lead-hours 1
.venv/Scripts/python.exe -m scripts.frost_local --local train --version rfd-candidate-h1-v1 --lead-hours 1
.venv/Scripts/python.exe -m scripts.frost_local --local infer --artifact instance/frost-local/candidates/rfd-candidate-h1-v1.json
.venv/Scripts/python.exe -m scripts.frost_local --local submit --origin https://neoapps.onrender.com --email YOUR_EMAIL --artifact instance/frost-local/candidates/rfd-candidate-h1-v1.json
```

`assemble` verifies every original byte hash, parser version, RecordID/revisions,
source provenance, local result receipt/hash and exact source-row content. It
reuses archived JSON, completed ZIPs and verified checkpoints. Only genuinely
new/changed rows receive local features; overlapping exports do not inflate
counts. It caches actual historical weather locally and never creates live spray
events. A private original directory containing the original filenames also
works; refreshed originals use content-addressed filenames to avoid collisions.

LOCAL DATA READY is derived from complete original/dedup/month reconciliation,
weather/classification coverage, explicit quality exclusions and code versions.
`prepare` adds actual eligible class counts, example SHA-256 and lead time.
Immutable manifest versions live in `manifests/<fingerprint>.json`; manifest.json
is only the current pointer. Altered/missing sources, files, counts, versions,
unresolved revisions or undisposed quality problems fail closed. There is no
force flag and production's old Phase 5 publication state is not consulted.

The two known gaps (document 4 rows 76/77, RecordIDs 215320/215321) are retained
as source-traceable exclusions from weather-dependent fitting. At 2018-10-17
00:39 Central the preceding observation is 105 minutes old, outside the 90-minute
contract; a future observation is not substituted. Exclusions do not imply a
frost-negative label or a failed operational transaction.

`refresh` checks the authenticated source catalog, downloads only missing/changed
originals and available result objects, verifies SHA-256 and rechecks the remote
snapshot. Optional `--with-weather` reads existing private observations, never
requests Render reconstruction. Unsafe transfers return 503 rather than spending
operational headroom; verified objects already downloaded are retained. CSV
imports update the source fingerprint, not the approved model. No automatic
training/backfill occurs. Reassemble locally after an incremental refresh.

All downloaded records/checkpoints/candidates default to the already ignored
`instance/frost-local/` directory. Use a protected workstation account/private
disk; operating-system access controls still apply. Repository-relative output
outside `instance/` or `_local_archive/` is rejected. No model binaries or source
datasets are committed. `infer` without a feature file performs the artifact's
numerical self-test, explicitly **not** a current weather forecast.

### Training, evaluation, and honest limits

One application/tail/night is the evaluation unit, not each participating truck
or fluid contribution. Original source-row/revision links remain in prepared
examples. Treatment START minus 0, 1, 3, or 6 hours defines the prediction cutoff;
lead-time preparation recomputes the same `frost_analysis.features` from cached
actual observations at or before that cutoff. No later observation is used.
Central calendar/DST rules remain Phase 5's authoritative contract. No weather
forecast is substituted for a historical observation.

Deterministic regularized logistic regression and a bounded 32-tree/depth-3
random forest are compared against a documented simple temperature/spread
reference and training-cohort climatology. Development uses forward whole-season
folds. The penultimate season is reserved for sigmoid calibration, the latest
season for untouched evaluation. A 24-hour boundary purge and night separation
prevent adjacent-event leakage. Imputation/scaling is fitted inside each fold;
missing indicators are explicit. The final model is **not refitted on the test
season**. Development checkpoints are reusable only for the same dataset, code,
dependency, feature, and lead-time contracts.

Reports include Brier/log loss, recall, false alarms/misses, reliability bins,
ECE, weather-regime slices, feature importance, and the single evaluated lead.
These measure the conditional treatment-reason target, not physical frost.
No confidence interval, physical frost prevalence, aircraft surface temperature,
or validated parking effect is invented. Historical reporting/selection bias
remains a limitation.

Initial **research promotion criteria**, not operational safety thresholds:
at least five seasons; untouched test with at least 100 applications and 20 per
class; recall >= 0.70; false-positive rate <= 0.30; ECE <= 0.10; Brier better than
climatology and no worse than the simple weather reference. All criteria and
DATA READY/fresh snapshot gates must pass. Changing research policy requires a
reviewed code change, not editing a candidate's pass/fail flag. Passing these
criteria does not by itself authorize operational frost forecasts.

Method references: [season/group validation](https://scikit-learn.org/stable/modules/cross_validation.html),
[calibration](https://scikit-learn.org/stable/modules/calibration.html),
[persistence security](https://scikit-learn.org/stable/model_persistence.html).

### Approval, rollback, and bounded serving

The authenticated FROST page imports candidates and reviews validation metadata.
There is no server TRAIN button. Upload never approves a model. Explicit
approval checks the local manifest/source-catalog compatibility, numerical runtime
self-tests, feature/classification versions, declared limitations and evaluation
criteria. Approval uses a gateway lock and expected registry revision; stale
conflicts preserve browser entries. Identical retries are idempotent. Rollback
is limited to a previously approved, still-compatible artifact.

The compact model contains the local validation manifest, source IDs/hashes,
quality dispositions, eligible class counts and training/evaluation metadata,
not historical rows. Admission verifies its hash/count reconciliation against
the current authoritative document catalog, current parser/feature/code versions,
season/lead contract and numerical self-tests. Evaluation policy is recomputed,
not accepted from a submitted PASS flag. This is an authenticated local-run
attestation, not remote reproduction of all backtests or proof of physical frost
skill. Explicit authorized review is still required; upload never approves.
Old already-approved legacy artifacts retain their bounded runtime/rollback
contracts. Model writes/approvals and bulk evidence reads respect the existing
75% memory ceiling and minimum 128-MiB headroom; no plan changes are made.

Artifacts are bounded (256 KiB) declarative JSON coefficients/trees. No pickle,
joblib, arbitrary Python deserialization, or executable model formats are
accepted. Each artifact and its exact dataset manifest is immutable,
content-addressed, read-back verified in private FROST `models/` / `manifests/`
namespaces. `frost_model_artifacts` stores only metadata; append-only
`frost_model_releases` attributes approvals/rollback to the authenticated user.
Canonical SQLite/PostgreSQL bootstrap creates and verifies both tables and
immutable-history guards, including upgrades from populated older schemas.

Inference is lazy, never a startup download. A process caches at most two
bounded models and 32 prediction results (five-minute reuse). Current observed
weather reuses the existing weather cache and official AviationWeather source,
with a bounded 14-hour/512-KiB/256-observation request; no historical seasons are
loaded. Missing/stale or incomplete weather yields an unavailable result.
Each result carries model/hash, source-weather and calculation timestamps,
target/lead, missing-input indicators, limitations, and weather contributions.
Only the artifact's validated observation-based lead receives a score; other
horizons and forecast-weather inputs remain explicitly unvalidated/withheld.
The pure forecast-feature adapter keeps forecast issue/valid time distinct for
future hindcast validation, but does not enable untested forecast scores.

An authorized manual calculation can persist an immutable FROST evaluation
record and small last-success metadata. Archive failures remain visible; retry
does not fabricate successful delivery. There is no new worker, recurring
training job, automatic promotion, plan increase, or change to live spray data.

### Verified local research run — 2026-10-27

11 byte-hash-verified originals cover seasons 2015–16 through 2025–26: 36,614
distinct RecordIDs, no overlapping imports/revisions in this catalog. All 12,797
existing local results were reused. The earlier 23,817 features were reconstructed
only on the workstation because large web evidence transfers lacked safe
headroom; all 65 previously published monthly results remain in FROST Vault.

Weather matches: 36,612; unmatched: 2. An additional 70 source rows have an
incomplete preceding-weather window (these flags already existed in the saved
local March-2024 result). All 72 quality dispositions retain exact source links;
none is a negative frost label. Classes at source-row level: 9,664 likely frost,
13,101 anticipated frost/pretreat, 13,841 other treatment, 8 unknown.

After aircraft/application/night grouping and exclusions, 20,627 examples are
eligible for the conditional treatment target: 8,427 frost-reason and 12,200
other explicit departure-treatment reasons. There are no credible untreated
physical-frost controls. Fit seasons end in 2023–24, calibration is 2024–25,
untouched test is 2025–26. Seven forward-season development folds compare logistic
and shallow forest candidates; forest was selected. The zero-hour lead test has
2,019 applications (690 frost-reason / 1,329 other), recall 0.85652, false-positive
rate 0.17231 (229 false alarms, 99 misses), Brier 0.09350, log loss 0.29345 and ECE
0.09833. Climatology Brier is 0.23310; weather-reference Brier is 0.27952. All
existing research gates pass, but calibration is near the policy boundary and
these metrics do NOT validate physical frost or future forecast-weather skill.

Candidate `rfd-treatment-h0-20261027-v1` is 141,824 bytes in ignored local
`instance/frost-local/complete/candidates/`. Its SHA-256 is
`f395ff1c85693e28d1349560faaf2985a6b2ed7b6dcbec11278dd13454c51e73`.
Dataset fingerprint is
`dce4665eeb13bbaf278c84e22be518f29f00d989aa24dabf0be6e5e0e371c1f2`.
Local training peaked at 587.33 MiB traced Python allocations; the bounded local
inference self-test used 0.76 MiB traced allocations. Neither is total process
RSS. No model was approved/activated, and no historical result rows were
published as part of this run. Model submission remains subject to live memory
headroom, authentication and explicit review.
