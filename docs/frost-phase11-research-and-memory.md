# FROST Phase 11 research training and memory hardening

Starting main and LIVE: `aed238395e98b190d62a1c29cd477b699bcb0983`.

Eight lead-specific candidates were fitted and evaluated **LOCALLY**. Their target is frost reason among recorded departure treatments, not physical aircraft frost. No candidate is submitted, approved, activated or used for DUCC warnings. Original CSVs, source evidence, weather caches and earlier models remain unchanged.

## Memory consumers and correction

Render runs one instance on the existing 512 MiB plan, using `gunicorn run:app`. Before this release, the container reported 481,636,350 bytes (459.3 MiB), while PID 80 reported 311,193,600 bytes RSS (296.8 MiB) and a 337,113,088-byte high-water mark. RSS is not container usage. Idle container usage was flat in the sampled 09:10–09:34 UTC interval; this is not evidence of an unbounded leak.

The shared application eagerly imported `parking_optimizer`, which imported OR-Tools CP-SAT and its NumPy/pandas dependencies even for an ordinary weather or FROST request. A fresh Windows import probe measured 60.84 MiB incremental working set. That is local attribution evidence, not a claimed Linux/container saving. The solver import now occurs only when an actual solve passes the existing candidate/model guards. All calculations, solver limits, actions and permissions remain unchanged. Existing diagnostic/test module access remains compatible.

Opt-in, rate-limited memory diagnostics now include cgroup anonymous/file/kernel counters, bounded PID/PPID/RSS/PSS snapshots and selected loaded-module flags. They never inspect environments, command lines, credentials or heap contents. File cache is distinguished from anonymous memory; shared RSS sums are not mislabeled as unique container usage. FROST standalone transport observations include client closure and collection on success/failure. No global periodic GC, malloc trimming, worker recycling or cache flush is used to conceal growth.

Current weather retains its existing finite provider-key/TTL cache. Private R2 reads/writes retain scoped client reuse, closed response bodies, client closure and existing retry/size limits. No demonstrated storage-client leak was found in this release. The PostgreSQL pool and operational background services are unchanged. Live SSH profiling was unavailable (`publickey` denied); bounded application diagnostics and Render container metrics provide the live evidence instead.

The 75% memory and minimum 128 MiB headroom guards are unchanged and fail closed. Heavy historical processing/training remains forbidden on Render. No service-plan, cost or concurrency increase is made. Warm/peak post-release readings belong in the ignored release-verification receipt; a low restart measurement alone does not prove long-term safety.

## Three independent readiness decisions

**Historical readiness:** the retained source-verified sample has 283 distinct operational nights, six forecast-covered seasons, 1,117 horizon-specific cases, 837/849 sampled cycles and 11,402 verified forecast body hashes. All 11 original Xchange documents and 36,614 historical rows are retained. Twelve explicit forecast gaps, two legitimate observation-age gaps and prior incomplete-window exclusions are not filled. The feature contract remains `krfd-forecast-native-v1`; the new sealed local assembly is `c5760b5e99389eeb5fec79105b755047e1fd6215632cba3f96437ea728ef7b61`. Assembly reused existing caches without a historical backfill or new archive download.

**Experimental eligibility:** `frost-native-exploratory-v2` permits pooled real samples from at least four chronological seasons, 150 independent nights and 30 examples per treatment-reason class. At least two forward folds must have 60 fitting nights/15 per class and 20 evaluation nights/5 per class. These are explicit feasibility floors for descriptive experimentation, not a power calculation, guaranteed precision or operational thresholds. No per-season 100-night quota is imposed: some complete seasonal populations cannot meet it. All actual fold counts and intervals are reported, and unsupported folds remain excluded. A fold with five positive cases has very wide uncertainty even with perfect recall. Forty-four inputs plus missingness against this sample remain a substantial overfitting risk.

**Operational approval:** still blocked. Retrospective fitting eligibility does not establish generalization, calibrated physical-frost probabilities or registry compatibility. The legacy strict independent-training path is retained separately. None of the new experimental JSON formats can pass the production model decoder. A genuinely new reserved/prospective evaluation cohort and adequate physical outcome evidence are required; repeatedly reviewed 2025–26 is not a new untouched holdout. No production registry or approval rule is relaxed.

Preprocessing uses fitting-fold-only means/scales and explicit missingness indicators. Each lead is evaluated separately; no pooling of four versions of the same night. Forward folds fit earlier seasons and evaluate 2022–23, 2023–24, 2024–25 and 2025–26, with a 24-hour boundary embargo. Fixed seed, fixed 0.5 classification threshold, regularized logistic regression and 32-tree depth-3 forests reuse Phase 6 fitting/scoring. There is no threshold search or post-hoc calibration on evaluation data. Compare the pre-specified temperature/spread reference and fitting-only class prior. [scikit-learn leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html), [temporal validation](https://scikit-learn.org/stable/modules/cross_validation.html).

Rate intervals use 95% Wilson intervals. Brier intervals resample nights within seasons with a fixed seed, conditional on observed season sizes. Serial weather dependence can increase true uncertainty, and these intervals do not estimate performance on a new season. ECE/reliability bins are descriptive, especially with small bins; no calibrated physical probability is claimed. [NIST proportion intervals](https://itl.nist.gov/div898/handbook/prc/section2/prc241.htm).

## Actual local candidates and evaluation

At 0/1/3 hours, final research fits use 280 nights (83 frost-reason / 197 other-treatment). The first two seasons warm up fitting (80 nights, 22/58); the four forward evaluation folds contain 200 different nights (61/139). Later fitting populations are 110, 141 and 172 nights. At six hours, the corresponding final fit is 277 (83/194), initial fitting 78 (23/55), later fitting 108/139/170, and evaluation 199 (60/139). Final local refits use all historical nights; out-of-fold metrics characterize the procedure, not independent validation of that final refit.

| Lead | Candidate | Evaluation nights | Recall | 95% recall interval | False-positive rate | Missed / false alarms | Brier | ECE |
| --- | --- | ---: | ---: | --- | ---: | --- | ---: | ---: |
| 0h | Logistic | 200 | 78.7% | 66.9–87.1% | 16.5% | 13 / 23 | 0.1256 | 0.0674 |
| 0h | Forest | 200 | 47.5% | 35.5–59.8% | 2.9% | 32 / 4 | 0.1433 | 0.1096 |
| 1h | Logistic | 200 | 75.4% | 63.3–84.5% | 16.5% | 15 / 23 | 0.1372 | 0.0767 |
| 1h | Forest | 200 | 50.8% | 38.6–62.9% | 6.5% | 30 / 9 | 0.1472 | 0.0941 |
| 3h | Logistic | 200 | 59.0% | 46.5–70.5% | 14.4% | 25 / 20 | 0.1414 | 0.0742 |
| 3h | Forest | 200 | 42.6% | 31.0–55.1% | 5.0% | 35 / 7 | 0.1563 | 0.0926 |
| 6h | Logistic | 199 | 53.3% | 40.9–65.4% | 15.8% | 28 / 22 | 0.1692 | 0.1212 |
| 6h | Forest | 199 | 31.7% | 21.3–44.2% | 7.2% | 41 / 10 | 0.1639 | 0.0813 |

Meteorological reference Brier is 0.2290/0.2290/0.2140/0.2149 at 0/1/3/6 hours; fitting-prior Brier is 0.2141/0.2141/0.2141/0.2128. These paired descriptive comparisons are not a claim of independently validated superiority. Reports retain log loss, reliability bins, Wilson false-positive intervals, conditional Brier intervals, feature coefficients/importance, every fold and per-night out-of-fold scores. Raw training evidence/model binaries remain out of Git.

### Important weaknesses

Logistic above-freezing recall is 10/18, 12/18, 5/18 and 3/16 at 0/1/3/6 hours. Clear/humid subsets have only 16 other-treatment nights and false-positive rates of 37.5%, 50%, 43.75% and 50%. One-hour seasonal recall ranges from 45.5% to 92.6%; three-hour recall is only 36.4% in 2023–24. Six-hour logistic Brier reaches 0.2674 that season. Forests miss substantially more frost-reason events while producing fewer false alarms. Precipitation subsets have 0–3 positives, too few for meaningful recall claims. All records have some missing context; parking/exposure and surface temperatures remain unknown. No candidate is chosen for promotion.

All four horizons now have exploratory treatment-reason evaluations. None has independently validated operational warning skill, and no horizon supports a calibrated physical-frost probability.

## Repeatable workstation workflow

```powershell
# Reuse existing evidence; this is offline assembly, not historical retrieval.
.venv/Scripts/python.exe -m scripts.frost_forecast_native --local --output instance/frost-local/phase11 --forecasts instance/frost-local/phase8/hrrr --forecasts instance/frost-local/phase10/hrrr-2020-2021 --forecasts instance/frost-local/phase10/hrrr-2021-2022 --forecasts instance/frost-local/phase10/hrrr-2022-2023 --forecasts instance/frost-local/phase9/hrrr-2023-2024 --forecasts instance/frost-local/phase9/hrrr-2024-2025 assemble
.venv/Scripts/python.exe -m scripts.frost_forecast_native --local --output instance/frost-local/phase11 validate
.venv/Scripts/python.exe -m scripts.frost_forecast_native --local --output instance/frost-local/phase11 explore --lead-hours 1 --version rfd-native-h1-NEW-EXPERIMENTAL-VERSION
```

Use a new immutable version for each run and repeat for 0/3/6 hours. `instance/frost-local/phase11/research/` contains sealed reports; `candidates/` contains eight JSON artifacts, about 9.4–27.6 KiB each. Existing Phase 6–10 artifacts remain untouched. Render cannot invoke these workflows. Research candidates are deliberately incompatible with the existing approval decoder and are not submitted.

## Physical outcomes and prospective collection

Optional PRESENT/ABSENT/UNCERTAIN inspections, actual tail/Central time, authenticated observer, treatment context, parking/exposure evidence, cached weather and append-only corrections are unchanged. Generic CLEAR, NEGATIVE DEICE or missing activity never become automatic negatives. Production read-only counts and model hashes are rechecked at release; no synthetic live observation is created.

The local `refresh-outcomes` command reuses content hashes and retains original revisions. Duplicate source IDs, repeated cursors or mismatched payload identity/schema now reject an incomplete refresh without replacing the previous complete catalog. A failed refresh may retain verified immutable source bodies, not falsely publish a complete catalog. Downloaded catalog absence still does not prove production absence.

```powershell
.venv/Scripts/python.exe -m scripts.frost_forecast_native --local --output instance/frost-local/phase11 refresh-outcomes --origin https://neoapps.onrender.com --email YOUR_AUTHORIZED_EMAIL
.venv/Scripts/python.exe -m scripts.frost_shadow_collect --local --once
```

Authentication prompts locally; credentials are not saved or extracted from browsers. Collection checkpoints and explicit offline gaps remain under `instance/frost-local/phase8/prospective/`. An optional Windows Task Scheduler task can use program `Z:\DevProj\NeoApps\.venv\Scripts\python.exe`, arguments `-m scripts.frost_shadow_collect --local --once`, Start in `Z:\DevProj\NeoApps`, hourly trigger while the workstation is available, and **Do not start a new instance**. Mapped Z: must exist for that user/session; otherwise use the actual local path. Do not store production credentials in task arguments. No task is installed or enabled by this release, and no Render research worker is added.

A real bounded capture succeeded at `2026-10-28T10:09:04.628314Z`, snapshot SHA `aebe992655c9d53adc81fec0383ec7cc30118b5f832f4306795df4a0c77cc1ba`. This collector's missed interval is explicit in its journal. The earlier Phase 10 capture in its separate research location is retained; a gap in one collector is not a claim that all local evidence was absent. No subsequent credible operational outcome pairing is yet available.

## Validation and release

Focused tests cover the versioned experimental versus independent gates, actual fitting reproducibility, night/season isolation, missingness, nonfinite/stale/mixed-horizon rejection, local/request-context enforcement, immutable research decoding rejection, outcome refresh/cache integrity, cgroup/PSS diagnostics and deferred solver startup. The existing parking optimizer regression group passes all 95 tests, including guarded/failed solvers. PostgreSQL/inspection/archive/model/weather and Chromium/WebKit regression results, exact SHA/deployment, authenticated routes, assets and sustained memory observations are recorded in the local release-verification receipt. No schema migration is needed.
