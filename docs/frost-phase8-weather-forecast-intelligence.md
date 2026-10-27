# FROST Phase 8 — versioned weather correction and LOCAL forecast research

Starting main/LIVE: `5cafdd10f33417b98ac242e651574e331cb90b6d` (2026-10-27).

## Decision and limits

The historical normalization defect is corrected, the complete local evidence is reprocessed, and frozen/retrained model comparisons are reproducible. **No model is approved, activated or presented as a physical-frost probability.** Genuine NOAA forecast evidence now has a bounded point-extraction and available-by verification path. Existing H0/H1 models still lack a validated forecast-domain contract; three/six-hour warning skill is not established.

The target remains frost reason **among recorded departure treatments**. These exports contain no credible untreated comparison population. Pretreat is anticipated risk, other spray reasons are not physical frost-negative controls, and absent activity is unknown. Neither ordinary CLEAR nor parking guesses become labels. Additional independently recorded frost/no-frost inspections, prior treatment, arrival/exposure/cold-soak information and reliable stand observations are needed for a physical-frost model.

## Corrected observation contract

FAA METAR encoding expresses `VVhhh` in hundreds of feet AGL. `VV014` is 1,400 feet, not a missing ceiling. IEM cloud codes may be padded (`VV `); IEM `skyl*` values are already feet. The new parser strips cloud-code padding and decodes numeric vertical visibility from the original report body; `VV///` remains unknown. Remarks are not interpreted as primary sky layers. Ordinary BKN/OVC layers retain their feet units and the lowest valid ceiling. Raw reports are unchanged. [FAA AIM, Meteorology](https://www.faa.gov/air_traffic/publications/aim_html/chap7_section_1.html).

Versions:

| Contract | Previous | Corrected |
| --- | --- | --- |
| Observation parser | `iem-rfd-routine-special-v1` | `iem-rfd-routine-special-v2` |
| Historical features | `frost-history-v2` | `frost-history-v3` |
| Model vector input contract | `frost-weather-vector-v1` | `frost-weather-vector-v2` |

Feature names/units and classification mappings are unchanged. The revised contract identifies the corrected input semantics. Legacy parsing remains explicitly selectable for immutable evidence replay. Legacy artifacts decode but cannot silently use corrected production inputs or bypass current local validation/approval gates. Older archive links and health totals remain accessible under their original version; they are not falsely relabeled as newly processed data.

### Full impact, not just the sampled discrepancy

- All 11 original document hashes and all 95 monthly raw weather hashes verified; 36,614 distinct Xchange rows across seasons 2015–16 through 2025–26.
- 84,894 observations checked; **1,126** changed observations.
- **419 source rows / 684 fluid windows** affected. Only ceiling and cloud category vector values changed.
- **266 H0 / 310 H1** independent application vectors changed across the complete eligible dataset.
- Required reproduction: document 1 row 1228, RecordID 162882, local application date 2016-03-04, selected raw `KRFD 050154Z ... VV014 ...` now retains `cloud_layers=["VV"]`, `ceiling_ft=1400` for both contributions.
- Eligibility unchanged: H0 **20,627** applications (8,427 frost-reason / 12,200 other); H1 **20,623** (8,427 / 12,196).
- Two legitimate weather gaps remain: doc 4 rows 76/77, RecordIDs 215320/215321, 2018-10-17 00:39 Central. Preceding observation is 105 minutes old; the next is future. No fabricated match.
- 70 incomplete prior-window rows remain excluded. Missing identity/provenance excludes 20 rows; H1 has four additional unavailable lead-time examples. Pretreat/unknown/ambiguous applications are not converted to negatives.
- Raw class totals unchanged: 9,664 likely frost, 13,101 anticipated/pretreat, 13,841 other treatment, 8 unknown, zero credible untreated controls.

Private details: `instance/frost-local/phase8/normalization-impact.json` and `frozen-replay.json`. Original `complete/`, `backfill/`, Phase 7 evidence and every earlier candidate remain intact.

## Local model comparisons

The original v1 artifacts are byte-identical to the imported models. An explicit LOCAL sensitivity replay on corrected inputs changes neither model's held-out binary decisions; Brier changes are negligible (H0 0.0935017741592; H1 0.1160084157996). This is not a production reinterpretation of their old feature contract.

New candidates use the same fixed algorithms, development-fold selection, seed, season separation, 24-hour boundary purge and calibration protocol. Fit: 2015–16 through 2023–24; calibration: 2024–25; final test: 2025–26. Seven forward development folds are retained. No threshold/hyperparameter was adjusted to improve the final test. Repeated correction evaluation does not make this an independent future holdout for subsequent model development.

| Untouched-fit 2025–26 evaluation, 2,019 applications (690 frost-reason / 1,329 other) | Original H0 | Corrected H0 | Original H1 | Corrected H1 |
| --- | ---: | ---: | ---: | ---: |
| Recall | 85.65% | 85.94% | 83.62% | 82.90% |
| False-positive rate | 17.23% | 15.80% | 21.52% | 22.42% |
| Misses | 99 | 97 | 113 | 118 |
| False alarms | 229 | 210 | 286 | 298 |
| Brier | 0.093502 | 0.092192 | 0.116008 | 0.117172 |
| Log loss | 0.293451 | 0.290954 | 0.349034 | 0.351774 |
| Calibration error (ECE) | 0.098329 | 0.074220 | 0.077551 | 0.088751 |
| Above-freezing recall | 4/26 = 15.38% | 4/26 = 15.38% | 14/34 = 41.18% | 12/34 = 35.29% |

Both corrected candidates pass existing research gates; the H1 result worsens. Warm subsets remain small/correlated (H0 460 applications on 58 nights; H1 466 on 60). H0 recognition is not advance warning; H1's training contract is actual observations one hour before START, not forecast weather at START. No physical-frost or operational warning claim follows from these metrics.

Final selected LOCAL research artifacts:

- `instance/frost-local/phase8/dataset/candidates/rfd-treatment-h0-20261027-v4.json`
- `instance/frost-local/phase8/dataset/candidates/rfd-treatment-h1-20261027-v4.json`

Intermediate v2/v3 candidates were retained. Final v4 artifacts attest identical feature vectors after legacy-refresh/cache version safeguards; research metrics did not change. `research-candidates.json` explicitly selects two hashed artifacts for LOCAL experiments only. It is not a production release. Compact artifacts remain local, not submitted or approved. `model-comparison.json` contains complete fold, regime, calibration and importance evidence.

## Authentic historical forecast extraction

The NOAA HRRR archive contains operational forecasts, not reanalysis. The [NOAA open-data catalog](https://registry.opendata.aws/noaa-hrrr-pds/) documents open access, attribution/no endorsement and archive coverage since 2014. A cycle/reference time alone is **not** publication time.

The extractor retains original NOAA object HEAD metadata. `Last-Modified` is treated only as a conservative archive-write/available-by bound, never an invented exact issue time or operator receipt. A case is accepted only if the original corresponding forecast-hour file's recorded write is between the cycle and prediction cutoff. A late bulk archive write is excluded, even if the model probably ran historically. Example: a 2015-10-30 file was written to this archive in July 2020; a 2025-12-01 00Z F01 file records 00:53:05Z that day.

One original-object probe per season: 2015–16 through 2019–20 cannot establish timely availability from their late archive writes; sampled 2020–21 through 2025–26 writes precede their cutoffs. This is **sampled availability**, not uninterrupted season coverage. MOS still has runtime/valid-time data but no established dissemination timestamp in the inspected response; TAF lacks the thermodynamic fields needed for these models. Neither is silently substituted.

For efficient geographic extraction, [University of Utah's NOAA-derived HRRR Zarr documentation](https://mesowest.utah.edu/html/hrrr/zarr_documentation/html/python_data_loading.html) supports one 150×150 spatial chunk rather than complete CONUS arrays. Only requested variables are loaded, decoded one at a time locally. The forecast grid's coordinates, projection, actual reference time, periods, valid times, array dimensions and units are checked. Raw chunks/metadata are content-addressed and retained. [Documented precision changes and missing-array limitations](https://mesowest.utah.edu/html/hrrr/zarr_documentation/html/zarr_variables.html) are preserved, not converted to zero.

Independent original NOAA GRIB range checks for TMP and UGRD at 2025-12-01 00Z F01 agree with the derivative point within .001 native units. ecCodes and the projected coordinate arrays select x=1130/y=677, **42.203089958°N, −89.099534260°**, approximately 1.62 km from KRFD (42.19151, −89.08760). Grid-relative winds are rotated using verified Lambert convergence (5.2294° at that cell). The sample verifies this grid/precision convention; it does not establish byte equality for every older float16 archive value.

Verified source fields: 2 m temperature/dew point (K→°C); humidity derived from those forecasts; 10 m wind components and gusts (m/s→kt, earth-relative direction); total modeled cloud fraction (%); cloud-ceiling height (native meters, datum not silently equated to METAR ceiling); visibility (m→statute miles); one-hour precipitation (kg/m²→mm water); categorical rain/snow/freezing rain/ice pellets. Exact GRIB parameter identities can supply units omitted by the derivative metadata, using the [NCEP inventory](https://www.nco.ncep.noaa.gov/pmb/products/hrrr/hrrr.t00z.wrfsfcf02.grib2.shtml). Missing arrays remain missing. Cloud fraction is not METAR cloud category; modeled hourly precipitation is not observed hourly-reset `p01i`; categorical precipitation is not measured intensity/fog. These distinctions prevent a false complete-input skill claim.

`hrrr/evaluation.json` reports paired observed/forecast-input **diagnostics**, counts, weather errors, calibration, misses, false alarms, reference-rule scores and missing-feature counts for requested 1/3/6-hour warnings. Only pre-cutoff observations and archived forecasts feed prediction inputs; subsequent observations are used solely for the paired comparison. Unknown future history features are not filled with later observations. Forecast valid hours precede the exact target by less than one hour; there is no invented minute-level interpolation. Three/six-hour diagnostics do not extend H0/H1 validation.

## Repeatable workstation commands

Run in `Z:\DevProj\NeoApps` with the existing virtual environment. Optional packages live in `tests/requirements-frost-local.txt`, never production requirements.

```powershell
.venv/Scripts/python.exe -m scripts.frost_weather_correction --local rebuild
.venv/Scripts/python.exe -m scripts.frost_local --local --directory instance/frost-local/phase8/dataset train --version YOUR_NEW_H0_VERSION --lead-hours 0
.venv/Scripts/python.exe -m scripts.frost_local --local --directory instance/frost-local/phase8/dataset train --version YOUR_NEW_H1_VERSION --lead-hours 1
.venv/Scripts/python.exe -m scripts.frost_weather_correction --local select --h0 YOUR_NEW_H0_VERSION --h1 YOUR_NEW_H1_VERSION
.venv/Scripts/python.exe -m scripts.frost_weather_correction --local compare
.venv/Scripts/python.exe -m scripts.frost_weather_correction --local replay
.venv/Scripts/python.exe -m scripts.frost_hrrr --local --command availability
.venv/Scripts/python.exe -m scripts.frost_hrrr --local --command verify-grib
.venv/Scripts/python.exe -m scripts.frost_hrrr --local --nights 120 --budget-mib 1024
.venv/Scripts/python.exe -m scripts.frost_hrrr --local --command evaluate --baseline-directory instance/frost-local/complete
```

Choose a **new** model version, never overwrite an existing candidate. `rebuild` is the one-time parser migration from protected Phase 6 evidence. Future monthly/seasonal uploads use existing authenticated `frost_local refresh`, then `assemble` over the updated source catalog and current cached weather/results; overlapping RecordIDs/revisions reconcile normally. Legacy downloads retain their own versions and cannot satisfy corrected training readiness without local assembly. Code-only re-attestation can use `frost_weather_correction revalidate`, which reuses verified monthly results rather than recalculating unchanged features. No bulk result upload is required.

Retrieval is LOCAL, hash checked, budget limited, timeout bounded and restartable. Four short-lived pooled network clients prefetch small chunks; all close after each cycle, including failure. Cooperating processes serialize atomic cache writes. Read timeouts retain completed cycles; rerun later rather than ignoring missing evidence or using a tight retry loop. A 403/404 cycle remains an explicit gap. No paid provider, Render plan change or cloud worker was added.

## Prospective collection and workstation scheduling option

One command captures METAR, newly issued TAFs and the current NWS grid, hashes original bytes, scores only LOCAL research candidates and records unknown outcomes:

```powershell
.venv/Scripts/python.exe -m scripts.frost_shadow_collect --local --once
```

For a bounded foreground run while this workstation is available, use `--count 8 --interval-seconds 3600` instead of `--once`; Ctrl-C stops it. Default capture spacing is hourly; allowed range 5 minutes–24 hours. A kernel file lock excludes overlapping collectors. Repeated invocations within the interval reuse the last success. Failed acquisition returns nonzero, retains prior success/checkpoints and logs the failure type without private provider bodies. Missed/offline intervals are recorded as gaps, never retrospectively fabricated forecasts or frost-negative outcomes. The live TAF query now uses each capture's cache, correcting the old all-day cache reuse that could miss amendments.

Optional **Windows Task Scheduler** setup, to be installed by the user if desired (not installed by this release):

- Program: `Z:\DevProj\NeoApps\.venv\Scripts\python.exe`
- Arguments: `-m scripts.frost_shadow_collect --local --once`
- Start in: `Z:\DevProj\NeoApps`
- Trigger: hourly while the workstation/user session is available; run missed starts when available.
- Multiple instances: **Do not start a new instance**. No stored production credentials are required.
- Review `instance/frost-local/phase8/prospective/collector-state.json` and immutable `collector-events/` for success/failure/gaps. An unavailable mapped Z: drive produces a missed capture, not hidden cloud execution.

No recurring task, extra Render worker, historical prediction pipeline or operational DUCC warning is installed. The authenticated website shows a bounded dated research receipt, not a live local-training dashboard.

## Safety and verification

No schema change or production historical/spray/inventory mutation. All originals, prior archives and local artifacts remain. Both imported candidates remain unchanged and zero model release rows exist. Old publication tasks remain `local_only`; no retry date or historical Render execution is re-enabled. The 75%/128-MiB protections remain intact. New model submission/approval remains authenticated, size/hash/version/manifest checked, memory gated and explicit; this release submits neither candidate.

Focused tests cover parser replay, vertical visibility/missing/multilayer data, version pairing, read-only legacy attestation, archive access, publication timing rejection, grid units/rotation, missing versus zero precipitation, bounded decompression, source tampering/range enforcement, local-only guards, collector exclusivity/recovery/gaps/fresh TAF acquisition, existing model permissions and mobile CSV import. The old reprocessing test's hardcoded starting version was replaced with the actual starting version while retaining immutable-evidence and no-extra-weather-fetch assertions.

## Completed historical forecast experiment

The requested maximum was 120 nights; the eligible held-out cohort has **108 distinct nights**, so all 108 were used, one first application per night. Night labels span 2025-09-03–2026-06-24 (actual Central application dates 2025-09-04–2026-06-25). Night labels are only validation groups; original local calendar dates are not reassigned. There are 27 frost-reason and 81 other-treatment cases at 1/3 hours. One unavailable Zarr cycle (`2026-05-06T23:00Z`) removes one frost-reason case at 6 hours, leaving 26/81 and 107 nights. No replacement case or future observation was selected.

**646 model-input/horizon cases**, 4,368 distinct source-body hashes verified. Source cache is approximately 2.21 GB, entirely LOCAL/ignored; no forecast archive is uploaded to Render. Original NOAA available-by metadata is before each accepted cutoff. Exact historical issue/receipt times remain unknown. **Zero contract-complete forecast-model validation cases**: these are genuine operational forecast sources but incomplete model-input/domain-transfer diagnostics, not validated probabilities or horizons.

| Corrected candidate / requested warning | Nights | Recall | False-positive rate | Brier | ECE | Paired observed-input Brier | Existing weather-rule Brier |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| H0 / 1h | 108 | 0% | 0% | 0.234105 | 0.223338 | 0.133863 | 0.212222 |
| H0 / 3h | 108 | 0% | 0% | 0.233497 | 0.222897 | 0.133863 | 0.201111 |
| H0 / 6h | 107 | 0% | 0% | 0.229206 | 0.218760 | 0.134847 | 0.202617 |
| H1 / 1h | 108 | 92.59% | 51.85% | 0.280968 | 0.332386 | 0.156357 | 0.178889 |
| H1 / 3h | 108 | 92.59% | 51.85% | 0.276781 | 0.320973 | 0.156357 | 0.184444 |
| H1 / 6h | 107 | 92.31% | 50.62% | 0.260766 | 0.303809 | 0.157110 | 0.197009 |

H0 misses all 27/27/26 frost-reason cases. H1 misses two at each warning but creates 42/42/41 false alarms among the 81 other-treatment cases. Above-freezing **forecast-input** subsets: H0 misses all 8/8/7 positives; H1 detects 6/8, 6/8, 5/7 with 29/27/29 false alarms. These small subsets differ from the full observed-input warm groups and cannot establish physical surface behavior.

The frozen original candidates were replayed on the **same** sources/cases under their legacy observation contract. H0 also misses every positive (Brier 0.243851/0.243537/0.237580). Original H1 recall is 96.30/92.59/92.31%, false-positive rate 61.73/66.67/61.73%, Brier 0.370123/0.378129/0.359189. The original one-hour paired observed Brier is 0.137875 (H0), 0.155512 (H1). Full 12-report metrics, calibration bins, log loss, per-case sources and missingness are retained privately.

For corrected H0's one-hour case endpoints, HRRR-minus-observed MAE is 1.211°C temperature, 1.117°C dew point and 2.397 kt wind; biases +0.195°C, +0.509°C and −0.209 kt. This is a coarse grid/valid-hour versus selected station-observation comparison, not an exact collocated sensor comparison. Model degradation must not be blamed solely on forecast weather error. Missing/incompatible cloud, precipitation, fog and future-history features, missingness patterns, treatment-selection bias and conditional calibration are substantial confounders. Continuous clearing, shifting-wind and surface-wetting skill remain unvalidated. There is no cross-season forecast-skill claim from this single held-out season; observed development folds are separately preserved.

Recommended next work: explicitly version forecast-domain features and missingness; use only genuine pre-cutoff cycles; develop lead-specific models with season/night-separated forecast folds; collect independent untreated/frost observations and aircraft exposure context; calibrate on new held-out prospective nights. Do not turn these diagnostics into DUCC warnings.

## Evidence identities and resource observations

Current H1 local dataset fingerprint: `085065db22aeb34e1b78379fc85317a8c3b40e89a56df174723b4d39feccde9a`. Source catalog remains `7a83f7cd7767a11ba7db052c615c1ae02bf2432125a0344d00d73e115f30614f`.

| Private artifact | SHA-256 | Bytes |
| --- | --- | ---: |
| H0 v4 candidate | `7c217e470196eae820cb52dd351a60345769e6854f0ec56c2867a1bb8dd52d90` | 142,336 |
| H1 v4 candidate | `604cf092f740bab997c3593f696600a4b1f9b7011e9ee303a57f035c1c27ad1c` | 120,879 |
| `normalization-impact.json` | `c0e3ded95464d6a8d2c5790fc81290ad977a24a31a68dfb255f4941fc57c7da8` | 562,089 |
| `frozen-replay.json` | `2ca87c4ccd7771894675edaf279dce7159ae2ae107da9e256911b57a0f9f7b58` | 335,077 |
| `model-comparison.json` | `bedb54d5e572b4cc0e4c47653f26c42a27d8e6db44237277d81a87a67e71d09f` | 106,767 |
| `hrrr/evaluation.json` | `2f946a5ec83863ee60e7f3d4b78f9f71b7ccbb4ef65588cf5e05d83f69443df9` | 3,035,451 |

Historical forecast case-body SHA: `5f0d3601fbdd743a9b297d5ba5a33171ffd5d12362f1af78e2c80c70eaaf3aef`. Intermediate sealed eligibility manifests reference the same original sources, weather and monthly features; this identity is checked when comparing H0/H1 manifests, not bypassed with a ready flag.

Training peaks: H0 587.11 MiB and H1 598.86 MiB **traced allocations, not total RSS**, on the workstation only. One sampled collector interpreter showed 206.82 MiB RSS / 226.09 MiB peak working set; its launcher used 14.22 MiB. A pre-release Render sample was approximately 369 MiB / 512 MiB. These are bounded observations, not a long-duration leak test.

Two real LOCAL prospective captures succeeded at 22:33 and 23:01 UTC. The journal records the intervening uncaptured interval as unknown. No scheduler is installed and no paired subsequent outcome is claimed. Temporary forecast request failures were resumed from verified caches; the unavailable cycle remains an explicit gap.

Validation: 191 Python/SQLite/disposable-PostgreSQL tests passed, followed by 14 focused final safety checks; 13 JavaScript checks passed. Three authenticated isolated browser workflows passed in Chromium and WebKit: research receipt/layout, complete mobile CSV selection/upload/error recovery, and model administration/drafts/permissions. Research viewport coverage: 1920×900 (primary), 1920×1080, 1366×768, 3840×2160, 1024×768, 768×1024, and 430/390/375/320×900. Python compilation and `git diff --check` passed. Existing deprecation/SQLite fixture resource warnings and deliberately injected retry traces are not new production exceptions. The two obsolete hardcoded version assertions initially failed, were corrected to preserve the real starting version, and passed on rerun.
