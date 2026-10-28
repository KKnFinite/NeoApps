# FROST Phase 9 — forecast-native research and explicit outcome evidence

Starting main/LIVE: `a34f38cb0cc20fdc18bf9cbf11d256f3f9ab31e0`.

## Decision

The research pipeline is implemented; **no forecast-native candidate is trained, approved or active**. The real dataset fails the independent-validation gate. This is not a claim that physical frost prediction is solved. No physical-frost probability, 0/1/3/6-hour warning skill or model accuracy is asserted.

The original 11 Xchange documents, 36,614 distinct rows, corrected 95-month observation evidence, prior model candidates and Phase 8 results remain intact. No full historical backfill was repeated. Original file hashes are rechecked during local assembly. Existing immutable corrected Phase 8 manifests may be read under their original audited implementation hash; that exception cannot certify fresh observation-model training or approval.

## Forecast-native contract

`krfd-forecast-native-v1`, with **44 ordered features** and separate contracts for 0, 1, 3 and 6 hours:

- `obs_*`: KRFD observations at/before prediction cutoff; temperature, dew point, spread, RH, wind/gusts, observed AGL ceiling, visibility, report age, 3/6-hour cooling, wetting/fog context and 12-hour coverage. Known future receipt times are excluded. Historic report time is not proof of exact historical operator receipt.
- `fcst_*`: HRRR target temperature/dew point/spread/derived RH, wind/gusts, modeled cloud percent, visibility, modeled one-hour precipitation and categorical precipitation. Wind directions use circular sine/cosine features.
- Forecast trends use only verified hours from the same cycle with independent pre-cutoff availability evidence. Unverified intermediate hours produce missing trends, not invented values.
- Forecast age, valid-hour offset, approximate solar elevation, night and hours since sunset are explicit. Exposure and prior pretreatment are usable only from a context snapshot recorded by the cutoff. No historical parking or aircraft surface temperature is guessed.

HRRR cloud percentage is not METAR sky category; modeled precipitation accumulation is not METAR `p01i`; model ceiling datum is unresolved and excluded. Missing values remain null with missingness indicators; fitting-fold means and scales never use validation/test observations. Two source-derived humidity values above 100% are retained in quality dispositions while the model feature is missing, not clamped.

Forecast evidence reuses the Phase 8 NOAA-derived Zarr point extraction and original NOAA object availability receipts. Grid x1130/y677 is approximately 1.62 km from KRFD. Raw source hashes, station, grid, units, cycles, valid times, available-by timestamps and source revisions are checked. Exact issue/receipt times remain unknown. Cycle time is never relabeled as issue time. [NOAA HRRR archive](https://registry.opendata.aws/noaa-hrrr-pds/) and [Utah archive variables/precision/missing-array documentation](https://mesowest.utah.edu/html/hrrr/zarr_documentation/html/zarr_variables.html).

## Actual assembled forecast population

| Season | 0h nights | 1h nights | 3h nights | 6h nights |
| --- | ---: | ---: | ---: | ---: |
| 2015–16 through 2022–23 | 0 | 0 | 0 | 0 |
| 2023–24 | 3 | 3 | 3 | 3 |
| 2024–25 | 3 | 3 | 3 | 3 |
| 2025–26 | 108 | 108 | 108 | 107 |

**114 distinct application/nights, 455 horizon-specific examples**. Counts by horizon: 0/1/3h each have 28 likely frost-reason positives and 86 other treatments; 6h has 27/86. These repeated horizon cases are not 455 independent nights. One unavailable six-hour cycle remains excluded. The six additional earlier-season nights were selected by deterministic first/middle/last timing, not scores. Retrieval was LOCAL and capped at 128 MiB per three-night season sample. No nationwide GRIB files were downloaded.

The remaining seasons have observation/treatment history, not assembled forecast-native coverage. Phase 8 availability probes cannot substitute for actual forecast cases or establish continuous coverage. Sparse three-night samples cannot support seasonal model evaluation.

## Targets, negatives and aircraft context

The historical target remains **frost reason among recorded departure treatments**, not physical frost occurrence. The complete historical source-row classes remain 9,664 likely frost, 13,101 anticipated/pretreat, 13,841 other treatments and 8 unknown. Forecast-eligible independent applications are a smaller population. Other treatment reasons are not physical frost-negative controls.

Read-only production audit found one planned, unstarted departure event, zero explicit NOT SPRAYED audit entries, zero actual spray contributions and no credible untreated population. No production test records were created. Confirmed physical frost positives and credible physical-negative controls remain **zero**. No physical probability model can be fit honestly from that population.

Prospective collection uses the existing **NOT SPRAYED** operational action, with no added mandatory control:

1. Existing permissions/current-sort/version safeguards still authorize the mutation.
2. A compact snapshot is added to the existing append-only audit only for an authenticated explicit outcome with canonical actual Block-Out, unchanged tail and no conflicting departure spray. Generic CLEAR, NEGATIVE, missing activity and cancellations do not qualify.
3. Same-tail actual Block-In establishes an exposure interval proxy; missing or post-swap arrival attribution stays unknown. Parking is included only if its canonical record predates Block-Out. Duration at the stand and measured surface exposure remain unknown.
4. Prior pretreatment is separate. Untreated current-sort context is stronger likely absence evidence, not physical confirmation; prior pretreatment is anticipated/confounded evidence, not a false forecast or strong negative. Outside-system treatment history remains unknown.
5. The existing SubZero outbox writes only the compact observation into private FROST `frost-observations`. It does not archive a full unsprayed CLEAR/NEGATIVE workflow. No second worker, permanent activity-log table or migration is introduced.
6. Stable event identity, immutable content revisions, retry/read-back, current checksum and later-spray/cancellation retraction prevent duplicate/stale learning evidence. The existing 75%/128-MiB guard defers delivery, never CLEAR or FIN.

Authorized, bounded `/neosubzero/frost-vault/observations` catalog and per-ID downloads allow incremental LOCAL refresh by content hash. Pending/revised evidence is explicit. No-spray observations remain a separate research population: they are never injected as “other treatment” class zero. Physical-model eligibility remains false until independently designed and validated for that target.

## Training and evaluation gates

Reuse Phase 6 fitting, bounded JSON numerical runtime, metrics, forward-season splitting and calibration. Each horizon fits independently. Development selects logistic versus shallow forest by forward-fold Brier only; one later season calibrates and a separate final season evaluates. Night separation and a 24-hour boundary purge remain. Fitting-only imputation/scaling and explicit missingness are retained. Fixed seed and numerical reproducibility are tested using disposable synthetic data, not advertised as operational model results.

At least five forecast-covered seasons are required, with at least 100 cases and 20 of each treatment-reason class per season. The final holdout must be independently reserved **after the already reviewed 2025–26 cohort**. The real training command rejected the current dataset. There is no force flag, fallback to the reviewed season, native candidate import command or automatic approval.

Consequently, new candidate recall, false alarms, Brier, calibration and 0/1/3/6-hour skill are **not available**. The previous H0/H1 observed and HRRR transfer diagnostics remain as frozen comparison evidence on the website, explicitly identified as reviewed Phase 8 results, not a new untouched holdout. The framework compares native models with forecast weather rules and treatment climatology when gates pass. A fair head-to-head with old models on a genuinely independent new cohort is still future work.

## Reproducible LOCAL commands

```powershell
# Incremental, explicitly selected season. Separate outputs preserve older caches.
.venv/Scripts/python.exe -m scripts.frost_hrrr --local --season 2024-2025 --nights 3 --budget-mib 128 --output instance/frost-local/phase9/hrrr-2024-2025

# Offline assembly: verifies original CSVs, source bytes and existing weather.
.venv/Scripts/python.exe -m scripts.frost_forecast_native --local --forecasts instance/frost-local/phase8/hrrr --forecasts instance/frost-local/phase9/hrrr-2023-2024 --forecasts instance/frost-local/phase9/hrrr-2024-2025 assemble
.venv/Scripts/python.exe -m scripts.frost_forecast_native --local validate

# Authorized incremental outcome retrieval. Uses the existing interactive sign-in;
# no browser-cookie extraction or unrestricted production database credentials.
.venv/Scripts/python.exe -m scripts.frost_forecast_native --local refresh-outcomes --origin https://neoapps.onrender.com --email YOUR_ACCOUNT

# Only after independent forecast/data gates pass; currently refuses real fitting.
.venv/Scripts/python.exe -m scripts.frost_forecast_native --local train --lead-hours 1 --test-season 2026-2027 --version YOUR_NEW_RESEARCH_VERSION

# Existing bounded prospective acquisition; does not install a schedule.
.venv/Scripts/python.exe -m scripts.frost_shadow_collect --local --once --output instance/frost-local/phase9/prospective
```

Local artifacts: `instance/frost-local/phase9/dataset.json`, immutable `datasets/<fingerprint>.json`, `training-readiness.json`, separate seasonal forecast caches, `outcomes/` and `prospective/`. All remain ignored by Git. New Xchange uploads continue using the existing original/extraction catalog and local refresh/assembly contracts; unchanged seasons are reused. No automatic training follows an upload.

A real LOCAL prospective snapshot succeeded at **2026-10-28T04:20:22Z**. Later explicit outcomes are not yet paired. Existing source hashes, retrieval times, raw forecast bytes, versions, locks, checkpoints and offline-gap handling remain. No unattended workstation scheduler was installed. The existing NWS weather refresh cache was reviewed: it is lightweight presentation state, not a durable issuance archive. With production memory already around 434–442 MiB / 512 MiB, no additional server forecast-storage path was added. Existing local collection is the safe supported path.

## Validation and release boundaries

Focused tests cover observation/forecast separation, all horizon cutoffs, future receipts/context, missing versus zero, source tampering, out-of-range RH dispositions, separate no-spray populations, stale manifests, synthetic seasonal training reproducibility, registry rejection of native candidates, role protection, rollback, retries, memory deferral and concurrent PostgreSQL coordinators. Existing overnight operations, original archives, mobile imports and model approval protections are regression tested.

Chromium/WebKit authenticated fixtures cover 1920×900 primary, 1920×1080, 1366×768, 3840×2160, tablets and 320–430px phones. Browser screenshots are under `instance/browser-evidence/mobile-drawer/frost-phase9-*`. No operational UI redesign or production state-changing tests are performed. Static research metadata is a dated receipt, not a live training dashboard.

No schema change, new worker, plan change, model approval or live frost warning. Deployment is by main/AutoDeploy only. Exact release SHA, Render deployment, authenticated production routes/assets/logs and post-deploy memory are recorded separately in the release-verification receipt and final response.

Release checks: **175 broader Python/SQLite/disposable-PostgreSQL tests passed**, followed by **25 final Phase 9 tests** (including real concurrent coordinator transactions), **13 JavaScript tests**, and **3 authenticated Chromium/WebKit workflows**. Python compilation and `git diff --check` passed. The initial broader/browser invocations included two nonexistent test names; those were command-selection errors, not application regressions, and the corrected suites passed. An initial PostgreSQL invocation requested an uninstalled psycopg3 driver; rerun with the repository's installed psycopg2 driver passed. Deliberately injected archive failures and existing deprecation/resource warnings are not production errors.

Sealed local dataset fingerprint: `2ff3bb89d4849a8cb4649b4c452543f45edb1f09815592356fc9d5f07a108330`. Repeated assembly retained identical source/classification counts and no duplicate nights; all previous immutable dataset versions remain. The real reviewed-season training refusal is recorded in `instance/frost-local/phase9/training-readiness.json`; no candidate file was produced.

Next evidence required: substantial earlier-season forecast coverage, genuinely new reserved/prospective nights, explicit exposed untreated outcomes with prior-treatment context, and independent physical frost/no-frost inspections. Weather is primary; aircraft exposure is secondary; parking adjustments remain disabled.
