# FROST Phase 12 prospective validation

Starting main and LIVE: `8af42a9397e60fc1f03daeb43ba3bcf723c52a38`.

The workstation captures public KRFD weather, freezes research predictions and later evaluates separately attributed outcomes. Render exports bounded private archive evidence and renders small dated research metadata. It does not acquire historical forecasts, train models, run shadow scoring or issue operational warnings. No schema change, hosting-plan change or new background worker is required.

## Frozen research baseline

The eight Phase 11 artifact files and four original evaluation reports are copied byte-for-byte into ignored `instance/frost-local/phase12/frozen/`. Their raw SHA-256 values, sealed artifact identities, original metrics, six fitting seasons and fixed 0.5 thresholds are recorded in `baseline.json`. The feature contract is `krfd-forecast-native-v1`; the research policy remains `frost-native-exploratory-v2`. Model numerical parity checks run before freezing.

Baseline fingerprint: `b8f33680a91ae65b650b69b2c0872ed024356d3c741651e2e52d4a62bbafb783`, frozen at 2026-10-28 19:18:23 UTC. All eight raw model hashes are published in the bounded `app/data/frost_prospective_research.json` receipt and authenticated research panel. Existing artifacts, historical CSVs and caches are unchanged.

These models estimate frost reason **among recorded departure treatments**, not physical frost probability across all exposed aircraft. They remain unapproved, non-registry-compatible research candidates. Neither collecting predictions nor accumulating outcomes authorizes promotion. The repeatedly reviewed 2025–26 cohort is not a new independent holdout.

## Local commands

Run from `Z:\DevProj\NeoApps` with the existing workstation environment. No production database connection or stored password is needed for public weather collection.

```powershell
.venv/Scripts/python.exe -m scripts.frost_prospective --local freeze --candidates instance/frost-local/phase11/candidates --reports instance/frost-local/phase11/research --output instance/frost-local/phase12/frozen
.venv/Scripts/python.exe -m scripts.frost_shadow_collect --local --baseline instance/frost-local/phase12/frozen/baseline.json --output instance/frost-local/phase12/prospective --once
```

Freezing again re-verifies and reuses the existing baseline; it does not overwrite it. Future candidate versions require a separate baseline and evaluation population. The legacy collector without `--baseline` remains compatible, but its old observed-model diagnostics are not Phase 12 independent predictions.

Each new cadence slot retains the original METAR/SPECI, NWS grid context and TAF receipts, plus a bounded current HRRR point retrieval using the established extractor. Original NOAA object Last-Modified timestamps establish conservative available-by bounds, not exact issuance or operator receipt. Known issuance, retrieval and valid timestamps remain distinct. NWS/TAF context never silently replaces missing HRRR native inputs. KRFD identity, grid cell, units, raw bytes, hashes and normalized values are checked. No nationwide GRIB download occurs.

Observed weather must precede the cutoff, be within the existing 90-minute limit and provide the existing adequate preceding observation window. Each horizon uses its own feature contract and same-cycle forecast trends. Missing fields retain null/missingness; stale or unavailable core data produces **UNAVAILABLE**, never an invented score. All daytime/low-risk captures are retained regardless of later treatment activity.

One OS-level lock serializes capture. A baseline/cadence/slot identity prevents duplicate predictions after restart. Each snapshot is written with fsync and an atomic no-replace commit; a separate receipt records the post-commit timestamp. Recovery from a missing receipt uses the recovery time, never a backdated timestamp. Failed attempts and missed intervals remain visible. Unavailable predictions are not later filled with future weather. The workstation clock is an explicit trust limitation; hashes are not an independent trusted timestamp service.

## Optional hourly Windows scheduling

Generate XML only:

```powershell
.venv/Scripts/python.exe -m scripts.frost_prospective --local scheduler-xml --baseline instance/frost-local/phase12/frozen/baseline.json --output instance/frost-local/phase12/prospective --xml instance/frost-local/phase12/hourly-capture.xml --user "$env:USERDOMAIN\$env:USERNAME"
```

The generated task uses the current Python executable, repository working directory, interactive-token/least-privilege execution, hourly repetition, network availability, StartWhenAvailable, IgnoreNew overlap handling and a 20-minute maximum execution time. It does not wake an offline workstation or contain passwords. The collector's default cadence is one hour; its CLI supports bounded 5-minute to daily intervals.

**Not installed or enabled in this release.** Only after explicit authorization, the user may install and later remove it with:

```powershell
Register-ScheduledTask -TaskName 'NeoApps FROST Local Research Capture' -Xml (Get-Content -LiteralPath instance/frost-local/phase12/hourly-capture.xml -Raw)
Unregister-ScheduledTask -TaskName 'NeoApps FROST Local Research Capture' -Confirm
```

Run only while logged in. After an offline period, the next run captures current weather and records the gap; it does not reconstruct supposedly pre-event predictions. No training command appears in the task.

## Private outcome refresh and evaluation

The existing FROST administration authority controls both new read-only endpoints: `GET /neosubzero/frost-vault/prospective-outcomes` and its ID-specific download. Metadata pagination is limited to 100 rows per response. Downloads enforce gateway ownership, current delivered checksum, source identity, bounded size and the existing 75%/128-MiB memory guard. Original archive receipts and revisions remain intact. No new operational activity database is created.

```powershell
.venv/Scripts/python.exe -m scripts.frost_prospective --local refresh-outcomes --origin https://neoapps.onrender.com --email YOUR_AUTHORIZED_EMAIL --output instance/frost-local/phase12/outcomes
.venv/Scripts/python.exe -m scripts.frost_prospective --local assemble --baseline instance/frost-local/phase12/frozen/baseline.json --captures instance/frost-local/phase12/prospective --outcomes instance/frost-local/phase12/outcomes --output instance/frost-local/phase12/evaluation
.venv/Scripts/python.exe -m scripts.frost_prospective --local evaluate --dataset instance/frost-local/phase12/evaluation/dataset.json --output instance/frost-local/phase12/evaluation
```

Authentication reuses the existing local HTTPS/CSRF client and prompts securely for a password; credentials are not saved. Incremental refresh reuses verified objects. Each catalog version is retained; duplicate identities or corrupt hashes fail without replacing the previous complete catalog. State-changing production testing is prohibited.

### Matching policy fixed before outcomes

`frost-prospective-matching-v1` evaluates 2026–27 evidence after baseline freeze. The unit is physical tail / Central noon-to-noon operational night / horizon / model / evidence cohort. It does not use current flight assignment to reassign a physical tail's history.

For each tail/night/cohort, select the first eligible subsequent outcome by actual timestamp, breaking ties by source hash. Match only a scored snapshot with target time at or before that outcome and less than 60 minutes earlier. Its durable commit must precede the actual outcome. When multiple captures qualify, take the latest target, then source hash; never choose by model score or correctness. Effective lead minutes are reported, so zero-hour recognition is not called advance warning. Each primary metric uses the first matched tail per independent night, retaining secondary aircraft cases separately.

Separate cohorts retain:

- Actual departure treatment with its exact reason and canonical classification. Other-treatment class 0 means a different treatment reason, **not frost-free aircraft**.
- Explicit physical inspections: present, absent or uncertain, authenticated observer, actual tail/time/location, weather reference, exposure proxy, pretreatment and correction provenance.
- Credible explicit NOT SPRAYED with reliable exposure and known no prior pretreatment as weaker likely-negative evidence.
- Actual pretreatment and pretreatment-confounded outcomes, separately identified and excluded from unconfounded comparisons.
- Unknown outcomes, including missing activity, ambiguous reasons, cancelled aircraft and generic CLEAR. These never become negatives.

Corrected inspection roots retain original evidence and count only their active revision. Actual pretreatment archives can establish a preceding treatment context; missing treatment history is not assumed absent. All forecast snapshots and predictions remain in the dataset even without matched outcomes. Later captured METARs provide subsequent-weather context only and never rescore earlier predictions.

Authenticated production verification exposed the existing `subzero-spray/v1` convention: `start_utc` serializes a canonical naive-UTC database column without an offset suffix. The local adapter explicitly records that schema disposition and normalizes it to UTC without rewriting the original payload. This does not permit naive forecast or inspection timestamps. The retrieved 2026-10-28 08:27 UTC application predates the 19:18 UTC baseline, has no recorded reason and is not a prospective positive or negative.

The local evaluation reports sample counts, independent nights, positives/negatives, misses/false alarms, recall, false-positive rates and Wilson intervals. Conditional treatment-reason evaluation additionally reports Brier/log loss/calibration bins and night-level bootstrap intervals. Physical/no-treatment comparisons are explicitly transfer diagnostics; probability-calibration statistics are suppressed for those cohorts. Regime summaries cover above-freezing cases, low winds, clear/humid forecasts, rapid cooling, cloud clearing and recent wetting. Weather serial dependence can make nominal night-level intervals too narrow.

No finite sample automatically approves a model. Physical-frost validation still needs a representative, independently collected outcome population and a separately reviewed evaluation design. The current live evidence count is zero physical inspections and zero explicit no-spray observations; no prospective accuracy is claimed.

## Optional inspection access and research page

An authorized Outbound editor can select an aircraft and tap **OPTIONAL FROST INSPECTION** near the top of its existing details workspace. This opens and focuses the existing blank-result inspection form. It does not create a duplicate form, infer a physical inspection, save anything or affect FIN/CLEAR/gallons. Current permission, attribution, draft, stale-write and audit protections remain unchanged.

The authenticated FROST panel shows the frozen identities, dated local capture receipt, horizon counts, missing intervals and validation limitations. Current inspection aggregates are labeled separately from matched evaluation cases. It is not a live connection to a workstation; a dated metadata receipt must not imply that a scheduler is running or that outcomes have been evaluated. Private research scores are not shown as operational frost warnings.

## Initial evidence and limitations

The first capture at 2026-10-28 19:19:36 UTC retained 14 KRFD observations and ten HRRR forecast-valid hours. All eight frozen models had contract-complete inputs and produced research scores, two per horizon. Source bytes, original availability receipts, the verified KRFD grid and normalized fields passed local re-audit. There are no matched prospective outcomes or evaluated nights yet. One capture night is not evidence of predictive skill or season-long collection reliability.

The actual authenticated archive response passed canonical SHA-256 verification (`f3e18602370301dab03f2aa0ba2735986af35ea65fef3dfb725f00b7002c0ab0`). Local assembly excluded that one pre-baseline record and retained all eight predictions with UNKNOWN outcomes. The resulting initial evaluation fingerprint is `64e5936aac2d19fdee0dd93d4ae93efba8eae5ec136040fce1eaef7c0a67b491`; its metrics list is empty, not a fabricated zero-error score.

The scheduler XML is prepared but uninstalled. Continued collection depends on an available workstation and authorized scheduling. No daily retraining is needed. Operational physical-frost accuracy, parking shelter adjustments and model promotion remain blocked by the evidence limitations, not bypassed by this release.
