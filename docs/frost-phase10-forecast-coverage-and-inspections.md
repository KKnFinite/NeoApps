# FROST Phase 10 forecast coverage and physical inspections

FROST separates treatment-reason research from physical aircraft frost prediction. This release expands source-verified LOCAL forecast coverage and adds optional direct inspections through the existing operational audit and private FROST Vault outbox. It does not approve a model or change DUCC warnings.

## Historical forecast collection

The existing HRRR point collector supports incremental, evenly spaced independent operational nights. Increasing the selection retains previously collected cases and immutable case manifests instead of replacing an earlier sample. A checkpoint is written after each completed night and on orderly interruption. Verified source bodies and point arrays are reused; unavailable cycles remain explicit gaps.

`--native-hours` retrieves original NOAA object HEAD receipts for intermediate hours needed by the existing forecast trend features. It uses the same maximum of four LOCAL network clients, byte budget, retries, locks and closed sessions. These requests retrieve metadata, not nationwide GRIB bodies. Only archived hours demonstrably available before each cutoff contribute features. NOAA Last-Modified is a conservative available-by bound, not an exact forecast issuance or operator-receipt timestamp.

```powershell
# Workstation only. Keep each season in its established private cache.
.venv/Scripts/python.exe -m scripts.frost_hrrr --local --season 2020-2021 --nights 30 --budget-mib 512 --native-hours --output instance/frost-local/phase10/hrrr-2020-2021
.venv/Scripts/python.exe -m scripts.frost_hrrr --local --season 2021-2022 --nights 30 --budget-mib 512 --native-hours --output instance/frost-local/phase10/hrrr-2021-2022
.venv/Scripts/python.exe -m scripts.frost_hrrr --local --season 2022-2023 --nights 30 --budget-mib 512 --native-hours --output instance/frost-local/phase10/hrrr-2022-2023
.venv/Scripts/python.exe -m scripts.frost_hrrr --local --season 2023-2024 --nights 30 --budget-mib 512 --native-hours --output instance/frost-local/phase9/hrrr-2023-2024
.venv/Scripts/python.exe -m scripts.frost_hrrr --local --season 2024-2025 --nights 30 --budget-mib 512 --native-hours --output instance/frost-local/phase9/hrrr-2024-2025
```

Repeat a command to resume cached work, or explicitly increase the independent-night selection. No unattended schedule is installed. Prior Phase 8/9 cases, originals, weather archives and candidates remain intact.

Bounded original NOAA GRIB comparisons checked two parameters (2m temperature and 10m grid-relative wind) on one retained cycle from each priority season. KRFD grid identity, units and wind convention were checked against original messages. The 2020–23 samples use float16 derivative storage; the 2024 sample uses float32. Comparisons use half one stored ULP plus 0.001 original-packing tolerance, retaining both source values and errors. Ten successful comparisons are spot-checks, not a claim that every archived value was compared. Receipts are in each cache's `grib-verifications/`; nationwide forecast bodies were not downloaded.

## Local assembly and eligibility

```powershell
.venv/Scripts/python.exe -m scripts.frost_forecast_native --local --output instance/frost-local/phase10 --forecasts instance/frost-local/phase8/hrrr --forecasts instance/frost-local/phase10/hrrr-2020-2021 --forecasts instance/frost-local/phase10/hrrr-2021-2022 --forecasts instance/frost-local/phase10/hrrr-2022-2023 --forecasts instance/frost-local/phase9/hrrr-2023-2024 --forecasts instance/frost-local/phase9/hrrr-2024-2025 assemble
.venv/Scripts/python.exe -m scripts.frost_forecast_native --local --output instance/frost-local/phase10 coverage
.venv/Scripts/python.exe -m scripts.frost_forecast_native --local --output instance/frost-local/phase10 validate
```

Assembly verifies all source document hashes, original weather evidence, source revisions, forecast bodies, KRFD grid identity and cutoff availability. The new sealed dataset remains under ignored `instance/frost-local/phase10/`; previous dataset versions are not overwritten. `coverage-report.json` records class counts, independent nights by season and horizon, missing features, exclusions and eligibility reasons. It rejects duplicate nights within a horizon. Assembly reports eligibility separately for each horizon, never pooling four horizons to inflate seasonal sample counts.

The feature contract remains **krfd-forecast-native-v1**. More available intermediate hours improve feature completeness without changing feature meanings. The implementation hash and dataset fingerprint change. HRRR cloud fraction, precipitation and unresolved ceiling datum remain distinct from METAR measurements. Missing values are not zero. Aircraft exposure, parking and surface temperatures are not invented.

Training still requires five covered seasons, at least 100 independent cases and 20 examples per class in each used season, and a genuinely independent final holdout after the reviewed 2025–26 cohort. A sampled season with fewer cases does not pass those gates simply because its source archive exists. The 2023–24 and 2024–25 locally eligible historical first-treatment populations contain only 90 and 83 independent nights, respectively, before additional forecast exclusions. More retrieval alone cannot give those populations 100 independent nights. No thresholds were lowered.

Other-treatment examples are a comparison class for treatment-reason research, not evidence that untreated aircraft were frost-free. No physical probability is fitted from that comparison. Existing research models and approval history are unchanged.

## Optional physical inspection

Authorized Coordinator/Outbound editors can open an aircraft's Operations details and expand **FROST INSPECTION · OPTIONAL**. Choose an actual result, enter the Central calendar inspection time and location, and explicitly attest to a physical inspection. The result defaults blank, never to a model prediction. Available choices are frost present, no frost observed and uncertain. FIN, CLEAR, gallons and departure never depend on completing this form.

The record includes physical tail, authenticated observer, original mission context, actual inspection time, source attestation, notes and recording attribution. Verified canonical parking is opt-in. A parking record changed after inspection, or an old stand predating Block-Out, cannot establish the inspection location. Later tail assignments cannot supply historical mission identity. Same-tail actual Block-In supplies only an exposure-duration proxy; it is not a measured surface temperature or uninterrupted stand duration. Recorded preceding pretreatment is retained; otherwise prior treatment remains unknown rather than being guessed absent.

Only an already cached KRFD METAR preceding inspection by no more than 90 minutes can supply a weather reference. The compact original provider snapshot, raw METAR, hash and observation/retrieval times are preserved. Missing, stale, future or wrong-station weather stays unknown. Saving an inspection performs no weather-network request.

Repeated requests use a stable nonce and body hash. Concurrent inspectors serialize on the existing operation/mission locks; a stale expected inspection is rejected with the draft retained. Corrections require a reason and append evidence rather than rewriting the previous inspection. Each revision has a bounded private archive payload, including revisions corrected before their first delivery. Tail swaps do not reassign that history.

The existing `NeoSubZeroAuditEntry` and `NeoSubZeroArchiveDelivery` records are reused. No new aircraft activity table, schema migration, worker or prediction state is introduced. Archive retries retain memory safety and do not block the operational save. Private catalog/download permissions and source checks remain in force. The research page counts current physical inspection revisions once using SQL aggregates, without loading historical datasets on Render.

## Physical labels and future evidence

Direct physical frost and no-frost inspections are stronger evidence than a treatment reason or an explicit no-treatment outcome. Uncertainty remains unknown. Absence after pretreatment is prevention-confounded and is not an untreated control. Phase 9's explicit NOT SPRAYED capture is preserved; generic CLEAR, NEGATIVE DEICE, cancellation and missing logs still do not create automatic negatives.

Local outcome refresh accepts both compact evidence schemas, preserves hashes and revisions, and keeps them outside the other-treatment training class. Physical-model readiness remains blocked pending adequate exposed-aircraft comparison observations, forecast pairing and independent validation. Parking refinements, including possible wind sheltering near positions 1 and 5, remain disabled without verified geometry and outcome support.

## Prospective collection and production boundaries

The existing local shadow collector remains the supported capture path. A real manual capture succeeded at **2026-10-28T06:13:25.970211 UTC**; its immutable snapshot hash is `00dd22d697c8f0443e448d8922fd386642cbcc1b2e76260c431dff9484e87d72`. The missing workstation interval was recorded as a gap, not reconstructed. No scheduler or Render historical/forecast worker was installed.

Before this release, Render container memory measured approximately **480–482 MiB / 512 MiB** during 05:50–06:01 UTC. This exceeds the existing 75% / 128 MiB transfer-headroom safeguards; those guards remain enforced. No plan or cost change is authorized or made. A MotherBrain Google live-poll HTTP 500 at 05:08:20 UTC predates this implementation and is a separate integration issue, not a Phase 10 regression.

## Completed local coverage

The sealed dataset is `931bd62b91a921e8f0af723bb433fc1537d856124891fd29497e243f6b43f1c6`. It contains **283 distinct operational nights across six seasons and 1,117 horizon-specific cases**, versus Phase 9's 114 nights, three seasons and 455 cases. Different horizons lose different unavailable nights; their union must not be confused with 280 cases available at each of 0/1/3 hours.

| Season | 0h nights | 1h nights | 3h nights | 6h nights |
| --- | ---: | ---: | ---: | ---: |
| 2020–21 | 50 | 50 | 50 | 48 |
| 2021–22 | 30 | 30 | 30 | 30 |
| 2022–23 | 30 | 30 | 30 | 30 |
| 2023–24 | 31 | 31 | 31 | 31 |
| 2024–25 | 31 | 31 | 31 | 31 |
| 2025–26 | 108 | 108 | 108 | 107 |

Each of 0/1/3 hours contains 83 frost-reason and 197 other-treatment cases. Six hours contains 83 and 194, respectively. Across the union, 85 distinct treatment examples have frost reasons and 198 have other reasons; unavailable cycles change the membership, not just the count. None of those other reasons supplies a physical frost-negative control.

Sampled retrieval retained **837/849 cycles**: 11 unavailable 2020–21 cycles and the previous unavailable 2025–26 six-hour cycle remain excluded. **11,402 distinct forecast source-body hashes** were verified. Existing 2015–16 through 2019–20 probes did not establish pre-cutoff availability; that is not proof that every historical forecast for those seasons is unavailable. No large speculative download was attempted for them.

At 0/1 hours, 4/280 cases lack modeled cloud fraction, 7 lack gusts, 6 lack precipitation amount and 17 lack precipitation-type coverage. The corresponding 3-hour counts are 4/5/5/14; six-hour counts are 1/3/2/14 of 277. One derived RH value at 0/1 hours remains missing because it is outside 0–100. Forecast temperature, dew point and wind speed are present in retained cases. Measured aircraft surface temperatures and historical stands remain unknown. Detailed observed-feature missingness and source dispositions are retained in `coverage-report.json`.

All four training gates remain blocked: the five earlier sampled seasons lack required seasonal/per-class support and there is no independently reserved post-2025–26 holdout. No exploratory candidate was fitted and no operational skill metrics are claimed. The two imported original candidates retain their exact hashes and zero approval releases. All 11 original source documents, 36,614 records, prior models and archived weather remain intact.

Production's read-only audit found **0 direct physical inspections, 0 explicit NOT SPRAYED audits and 0 actual spray contributions**. The local operational-outcome catalog is explicitly marked `not_refreshed_locally`, not verified empty. Physical cohort counts in the local dataset describe downloaded evidence only; the current production count was checked independently. Future authorized `refresh-outcomes` retrieves new delivered observations securely, without changing the treatment-reason target.

## Validation and release verification

The broad Python/SQLite/disposable-PostgreSQL run passed **229 tests**, followed by **28 focused final Phase 10 tests**, including interruption/recovery and real concurrent inspectors. Eighteen JavaScript checks passed. Five authenticated Chromium/WebKit workflows passed: optional inspections, existing Operations, mobile CSV import, model administration and the final research summary. Viewports include 1920×900 primary, 1920×1080, 1366×768, 3840×2160, tablet and 320–430px phones. Compilation and whitespace checks passed.

Two new test-fixture issues were corrected: import order around an existing circular module boundary, and a mock that incorrectly returned an already-fingerprinted raw HRRR point. Browser testing reproduced and corrected idle live-refresh collapse of open details. A focus-stable browser measurement replaced a detached-element scroll helper. These are distinct from the pre-existing production Google live-poll HTTP 500. Deliberately injected archive outages are expected test evidence, not production failures.

No production spray/inspection test record, schema migration, model approval, hosting-plan change or background research worker was created. Exact AutoDeploy SHA, authenticated route responses, asset checks, bootstrap logs and post-release memory are recorded in the release-verification receipt.
