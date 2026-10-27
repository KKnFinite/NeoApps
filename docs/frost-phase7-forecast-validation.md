# FROST Phase 7 — forecast validation and local shadow research

Study: 2026-10-27. Starting main / LIVE: `92943f0e29104fd1a909f3970274f2159f125139`.

## Decision

**Meaningful operational advance-warning skill is not established. Neither candidate is approved or activated.** The reproducible research tooling and dated authenticated research receipt are implemented. A contract-complete historical forecast validation remains incomplete, not a passing gate.

The models estimate **frost reason among recorded departure treatments**, not physical frost probability across all aircraft. Other explicit departure causes are the comparison class, not verified frost-free aircraft. There are no credible untreated controls in these CSVs. Pretreat remains anticipated risk; absent treatment remains unknown. No historical parking positions or surface temperatures were invented.

## Reproduce on the workstation

Run from the existing repository with its local virtual environment:

```powershell
.venv/Scripts/python.exe -m scripts.frost_forecast_validation --local audit
.venv/Scripts/python.exe -m scripts.frost_forecast_validation --local sources
.venv/Scripts/python.exe -m scripts.frost_forecast_validation --local forecast
.venv/Scripts/python.exe -m scripts.frost_forecast_validation --local contributions
.venv/Scripts/python.exe -m scripts.frost_forecast_validation --local shadow
.venv/Scripts/python.exe -m scripts.frost_forecast_validation --local compare-shadow
```

Defaults: verified dataset `instance/frost-local/complete`; new private research evidence `instance/frost-local/phase7`. Both are ignored. `--directory` and `--output` support explicit local paths. All commands reject Render environments and web request contexts before work. There is no production training/collection endpoint or second worker.

`audit` checks immutable original/object/raw-source hashes and parser contracts. Missing earlier local CSV weather bytes are retrieved from the exact recorded public IEM request and accepted only if their original SHA matches. Existing objects are never overwritten. Public retrieval is timeout/size bounded, throttled and content-addressed. Cache tampering fails closed. HTTP/rate-limit failures stop with retained checkpoints; rerun after the provider's retry interval, not a tight retry loop.

`forecast` uses the unchanged bounded JSON candidates and actual issued KRFD TAFs with original bulletins. It keeps model input time separate from event time: H0 input at treatment START; H1 input at START minus one hour. A requested warning horizon limits the issue cutoff; future amendments/observations cannot fill inputs. Three/six-hour results are exploratory, not validated model horizons.

`shadow` performs **one local capture**, not continuous scheduled monitoring. It saves original current METAR, issued TAF, and NWS forecast-grid bytes, hashes, issue/revision time, valid time, retrieval time, KRFD containing-cell geometry and source receipts. Unapproved candidate scoring is confined to local RESEARCH output. Runtime unit validation uses the existing weather interval helpers; grid QPF/sky percentages are not misrepresented as measured METAR precipitation/cloud codes. Stale grids fail closed using the existing forecast stale limit.

`compare-shadow` verifies saved snapshot/source hashes, then replays the captured forecast against later explicitly recorded, source-traceable departure treatment evidence at each actual event's model-input time. Only pre-event captured observations/grid bytes are used. It never fills forecasts with subsequent observed weather. Missing subsequent treatment is UNKNOWN, never frost-negative. Replays are limited to the captured six-hour research domain and remain research-only. There is no automatic retraining, approval, or live DUCC warning.

## Observation audit

- Exact source: IEM RFD / IL_ASOS, ICAO KRFD, 42.19151 / −89.08760, America/Chicago. [Authoritative station metadata](https://mesonet.agron.iastate.edu/sites/site.php?network=IL_ASOS&station=RFD).
- 11 original Xchange CSV hashes verified; 36,614 distinct source records; seasons 2015–16 through 2025–26.
- All 95 monthly normalized weather objects and original raw CSV hashes checked. Exact raw normalization reproduced with the unchanged historical parser. These are airport METAR/SPECI observations, not reanalysis or another airport.
- 69 deterministic applications sampled across all 11 seasons: first applicable frost/other, above-freezing, very cold, clear, precipitation and March/November DST-neighborhood strata. Selection did not depend on model score. Every sample links document/hash/row/RecordID, actual local START, UTC conversion, selected raw METAR, age, preceding 12-hour window and trends.
- Maximum preceding observation age remains 90 minutes. Two genuine gaps remain: document 4 rows 76/77, RecordIDs 215320/215321, 2018-10-17 00:39 Central / 05:39 UTC. Prior report 03:54 UTC is 105 minutes old; next 05:54 UTC is future. No match fabricated.
- 70 existing incomplete-window records remain explicitly excluded from weather-dependent fitting. No source/model/feature data was rewritten.
- Independent raw-text checks decode temperature/dew point (including T remarks), winds, clouds/ceiling and recorded weather codes. Fahrenheit export precision is distinguished from raw Celsius precision; RH is derived, not directly reported by the METAR.
- **Confirmed normalization deficiency:** document 1 row 1228, RecordID 162882: raw `VV014` became cloud code `VV ` with trailing whitespace. The existing feature parser fails to recognize the vertical-visibility ceiling/category. One of the 69 sampled reports exposes this issue. It is not silently stripped to claim a pass. Correcting the feature contract requires versioned reprocessing and candidate re-evaluation; neither existing artifact was modified.
- IEM report timestamps do not prove exact historical operator receipt time. Later corrections and export availability are limitations, not presumed zero latency.

Private detailed report: `instance/frost-local/phase7/weather-audit.json`. It includes all source/month hashes and individual sample evidence. Do not commit private rows or original weather files.

## Authentic forecast availability

| Source | Verified retrieval | Issue/valid evidence | Disposition |
| --- | --- | --- | --- |
| IEM GFS MOS | One KRFD cycle in each of 11 seasons; each returned 21 forecasts | Response has runtime and ftime; no independently established publication time | Not accepted as genuine historical skill input merely because runtime precedes treatment |
| IEM KRFD TAF | 69 sampled applications across all 11 seasons; original product text retained | UTC issue timestamp agrees with product ID and raw KRFD header; raw validity and segment checked | Genuine issued forecast evidence, but thermodynamic inputs missing |
| NOAA HRRR | Small surface parameter indexes for 2015-10-30, 2020-12-01, 2025-12-01 returned HTTP 200 | Cycle/forecast offsets in index; no independent historic dissemination proof established here | No whole GRIB archive downloads; point extraction/availability proof still needed |
| Current NWS grid | KRFD points maps to LOT/25,85; containing polygon and forecast units checked | Source updateTime retained as grid revision time; actual retrieval and interval valid times also retained | Prospective shadow evidence, never a historical replacement |

Archive probes establish sampled availability, not uninterrupted eleven-season coverage. A missing provider field/period stays missing. No paid service, extra subscription, hosting upgrade or cloud worker was added.

Sources and documented limits:

- [IEM MOS archive coverage and encodings](https://mesonet.agron.iastate.edu/mos/) and [bulk download contract](https://mesonet.agron.iastate.edu/cgi-bin/request/mos.py?help). Runtime is not assumed to be actual public availability.
- [NWS short-range GFS MOS](https://vlab.noaa.gov/web/mdl/short-range-gfs-mos): station-specific guidance, normally six-hour-plus model projections; cycle terminology must not substitute for dissemination proof.
- [IEM TAF archive](https://mesonet.agron.iastate.edu/request/taf.php), documented back to 1996, and [API issue-time semantics](https://mesonet.agron.iastate.edu/cgi-bin/request/taf.py?help). Initial `Observation` rows in this export are TAF initial forecast groups, not observed METAR measurements. TEMPO/PROB alternatives are retained separately, not averaged into a deterministic prediction.
- [IEM original NWS text retrieval](https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py?help). A bounded MAVRFD probe for 2025-12-01 returned no original bulletin; this does not prove no other archive retains it.
- [NOAA HRRR operational system](https://emc.ncep.noaa.gov/emc/pages/numerical_forecast_systems/hrrr.php) and [NOAA open-data archive/license](https://registry.opendata.aws/noaa-hrrr-pds/): hourly, approximately 3 km, archive advertised since 2014. Open NOAA data require attribution/no implied endorsement; sample coverage does not guarantee every cycle.

## Frozen-model results

Both private local artifact hashes match the production imported candidates; zero release rows were present at baseline. No actual candidate was retrained.

| Untouched 2025–26, 2,019 applications (690 frost-reason / 1,329 other) | H0 forest | H1 logistic |
| --- | ---: | ---: |
| Frost-reason recall | 85.65% | 83.62% |
| False-positive rate among other recorded causes | 17.23% | 21.52% |
| Missed frost-reason cases | 99 | 113 |
| Brier | 0.09350 | 0.11601 |
| Log loss | 0.29345 | 0.34903 |
| Calibration error (ECE) | 0.09833 | 0.07755 |
| Above-freezing recall at model input time | 4/26 = 15.38% | 14/34 = 41.18% |

The H0 observed-input result is **not** advance warning. H1 was trained on actual observations available one hour before START, **not** one-hour-ahead predicted meteorology. Scores reproduced exactly after including preceding-month observations in the research replay.

The original warm subsets differ: 460 applications / 58 nights at H0, 466 / 60 at H1. Their frost-reason cases occur on only 8 and 10 nights respectively, all at night, with input temperatures approximately 0.56–5.56°C. H0 positive median spread 0.56°C and wind 4 kt; H1 median spread 2.78°C and wind 5 kt. Sparse, correlated nights limit inference. On the same 460 event-time-above-freezing applications / 26 frost-reason cases, H1 detects 10/26 (38.46%), versus H0 4/26. No claim that weather alone proves physical surface frost or explains every miss.

Historical forward development folds remain separate from the frozen final test. For example, 2021–22 FPR was 44.5% for the forest approach and 36.7% for logistic; the aggregate final score does not establish stable seasonal performance. Saved fold reports retain all season counts/metrics. No older fit/calibration season is mislabeled independent validation of the final fitted model.

Three deterministic cross-night feature-block rotations on the held-out season measured descriptive sensitivity. Mean Brier increases:

| Block | H0 | H1 |
| --- | ---: | ---: |
| Moisture/precipitation | 0.1849 | 0.0722 |
| Thermodynamics | 0.0367 | 0.0494 |
| Temperature trends | 0.0073 | 0.0143 |
| Clouds/visibility | 0.0028 | 0.0324 |
| Wind | 0.0006 | 0.0177 |
| Solar/night proxies | 0.0067 | 0.0079 |
| Fog | 0.0004 | −0.0031 |

Correlated features and treatment-selection bias preclude causal attribution. Strong precipitation dependence may distinguish reasons for an already-recorded treatment rather than identify frost on all parked aircraft. Surface temperature, cold soak and verified stand exposure are absent. No unverified position-1/5 sheltering rule was enabled.

### Actual issued-TAF diagnostic, not complete forecast validation

H0: 69/69 usable partial cases at each 1/3/6-hour requested warning. H1: 67/69 at one hour, 69/69 at three/six hours. Two conservative gaps occur where the latest issued bulletin is not yet valid at H1's input time; no older/future bulletin was silently substituted.

Only **7** sampled cases are from the untouched final season: 2 frost-reason, 5 other. Same seven cases for both candidates and all horizons:

| Partial-input experiment | 1-hour | 3-hour | 6-hour |
| --- | ---: | ---: | ---: |
| H0 Brier / recall | 0.2391 / 0% | 0.2390 / 0% | 0.2360 / 0% |
| H1 Brier / recall | 0.1312 / 50% | 0.1536 / 50% | 0.1120 / 50% |

Paired observed-input Brier on these seven cases: H0 0.22395, H1 0.17502; both recall 100%. Simple existing weather-reference Brier 0.21143 on partial TAF inputs, with no detected frost-reason cases. No threshold was changed. Missing T/dew/humidity and incompatible precipitation fields remain missing and use each unchanged model's existing imputation indicators. These are imputation stress tests, **not** a validated forecast-weather performance comparison. The seven-case sample cannot estimate operational calibration/recall reliably. There are **zero contract-complete historical forecast cases** in this study.

## Prospective status

One content-addressed local snapshot captured at 2026-10-27 20:11 UTC; one local replay finds zero subsequent explicit outcome pairs and reports UNKNOWN. Captured NWS forecasts contain thermodynamic values absent from TAF, but categorical/amount and future history-feature coverage still requires validation. Source bytes/metadata are preserved for later event-aligned comparisons; no inferred frost-free nights.

Continuous collection is **not scheduled** by this release. An authorized local invocation can be repeated when the workstation is available. Nothing trains, downloads archives or scores these unapproved candidates on Render. The production page displays only a bounded, dated summary; no operational status, model registry approval or prediction gate was changed.

## Next evidence and model work

1. Version and correct the vertical-visibility normalization; quantify its full-dataset impact and re-evaluate candidates explicitly rather than changing existing model feature meaning in place.
2. Establish genuine MOS/HRRR availability metadata and geographically verified, unit-safe parameter extraction; retain original forecast cycles and dissemination proof. Do not replace gaps with reanalysis or later observations.
3. Collect a substantially larger prospective season/night sample with preserved issued forecast revisions, including warm, humid, clearing and cold-soak regimes.
4. Obtain credible aircraft-level no-spray/frost observations and pretreatment/exposure context before claiming physical frost probabilities. Parking remains secondary and disabled without verified evidence.
5. Evaluate lead-specific future-weather features and calibration on new untouched nights/seasons; no retraining or threshold relaxation was performed here.

## Delivery and safety

- Changes: one pure bounded research contract module, one local CLI, small dated JSON receipt, private read-only download route, compact collapsible FROST section/CSS, focused tests and this report.
- No schema/model/operational record changes. No historical R2 objects moved/deleted; all originals, local results, candidates, stashes and artwork retained.
- New source/result details stay local and ignored. Only aggregate study metadata is deployed. CSV import, registry and private downloads remain available even if the study receipt cannot be read.
- Test evidence: 164 broader SQLite/PostgreSQL FROST/SubZero tests passed; final focused research tests additionally exercise exact raw checks, DST/90-minute boundaries, issued/valid times, partial/gap handling, stale grids, no future observations, local-only guards, hash tampering, read-only permissions, no activation and corrupted receipt recovery. Existing JS tests passed. Browser results and exact release verification are recorded in the delivery response after deployment.

Private research receipt hashes at study completion:

- `weather-audit.json`: `c5ab9a7e7b7bec2739f50dc22ed5921bf600ca9bd34d5c9dd2ea15f1a837b4af`
- `validation.json`: `e98a39e1ad4ac7aa3d5f21a1e685d2e78cd0d9bf9150f9d18b873e8bb3013618`
- `source-availability.json`: `51c9c0ef644a1101356388e4dcfbdbdce97b89105c759f674b22ab47950da6e6`
- `contributions.json`: `770ab219b1b94b49590143c46d9da04dae9117e76e65108e695c3d0fdad58703`

Repeated CLI runs produce a new dated study receipt; hashes are not falsely treated as timeless predictions or an approval attestation.
