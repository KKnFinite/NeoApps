# KRFD atmospheric frost assessment

The November 19 opening had weak weather support for frost at all four forecast horizons. By the midpoint, observations had cooled below the forecast overnight minimum. FROST flags that disagreement as uncertainty. The historical models alone cannot establish whether frost formed.

## Historical learning population

The 11 original Cryotech/Xchange uploads cover seasons 2015–16 through 2025–26. A local replay verified each file against its source-catalog hash. All 36,614 distinct records showed positive Type I or Type IV application. There were 9,664 records coded F, 13,101 coded P, 9,976 coded SN, 2,643 coded CS and 1,230 with other reasons. Of 4,127 populated notes, 33 mentioned non-application by a truck or of a particular fluid. The corresponding aircraft received treatment. These sources establish **zero verified untreated departures**. Departure schedules, unmatched Block-Outs, absent uploads and missing notes leave the treatment outcome unknown.

The original preparation code grouped applications by operational night, tail and application time. It required traceable source records, consistent known departure-treatment labels and complete prior weather. Pretreat, ambiguity and unknowns were excluded. The resulting 20,623 groups contained 8,427 F-coded and 12,196 other treated cases. Forecast acquisition grouped by season and night, selected the earliest eligible application on a night, and sampled spaced nights within its historical retrieval budget. Forecast assembly additionally required as-of KRFD weather and archived HRRR availability at the horizon cutoff. H0, H1 and H3 each retained 83 F and 197 other cases. H6 retained 83 F and 194 other cases. These counts have different units from the raw truck records. See `scripts/frost_local.py`, `scripts/frost_hrrr.py` and `scripts/frost_forecast_native.py`.

The fitted outcome is the recorded reason conditional on an eligible treated departure. Other-treatment cases can teach discrimination between reasons. They cannot establish frost absence. NeoSubZero Sorts contain testing and pretreat activity and supply **no atmospheric frost outcomes**. They contribute neither training examples nor validation cases to this assessment. Actual operational records remain available in their own history.

## Meteorological method

For each H0/H1/H3/H6 target, use the as-of KRFD observation, observation trend, target HRRR temperature and dew point, humidity, cloud coverage, wind, same-cycle trend and local night indicator. Cross-check the NWS hourly air forecast and the issued NWS minimum period covering the target. The period low comes from the same archived grid request. Evidence records its validity interval. A missing, ambiguous or future period cannot supply a minimum.

* For subzero dew points, compute the vapor pressure over water and invert the ice saturation equation to find the frost point. For positive dew points, freezing bounds ice deposition. Output the additional surface cooling that would be needed. FROST has no measurement of an aircraft or exposed surface temperature. [WMO Guide to Instruments, Annex 4.B](https://www.weather.gov/media/epz/mesonet/CWOP-WMO8.pdf)
* Clear skies, light wind and a cooling trend can support local radiative cooling with above-freezing air. The regional NWS guidance describes typical frost around 33–36°F, patchy frost into the low 40s, and a representative 36°F air / 30°F near-surface example. Our six-degree comparison represents that example. Stronger local cooling can occur. The upper guidance range has no force as a physical impossibility threshold. [NWS La Crosse](https://www.weather.gov/arx/why_frost) · [NWS Omaha](https://www.weather.gov/oax/freeze)
* Use up to 30% sky cover for the mostly clear regime, 70% or more for mostly cloudy, and roughly 8 mph as a regional indicator of mixing. A cool target near saturation, locally favorable nighttime cooling, and warm guidance with widespread cloud or mixing yield different assessments. RH and dew point describe the same underlying moisture. Inconsistent subfreezing saturation evidence requires uncertainty. [NWS sky-cover guidance](https://www.weather.gov/mfl/analysisinfo) · [NWS Omaha](https://www.weather.gov/oax/freeze)
* Mixed signals, missing trends and observed cooling below a warm forecast low warrant uncertainty. Missing essential weather or a valid overnight low makes the assessment unavailable. The weather regime and explanatory inputs are determined independently of the classifiers. These rules adapt regional guidance to a qualitative KRFD assessment. They carry no locally calibrated probability, surface forecast or treatment instruction.

The operator sees the weather assessment first. Each historical classifier retains its frozen score and existing 0.5 decision boundary. A high score conflicting with weak physical support receives an explicit reliability flag. High scores with mixed or missing weather remain unconfirmed. Opposite class decisions are shown together. A low score cannot dismiss ice-saturated, subfreezing conditions.

## November 19 RFD Night replay

We verified both capture hashes, original NWS grid bytes and all eight registered artifact hashes, then rescored the PR #50 corrected model vectors offline. The table gives percentages for the historical treatment-reason class. The same scores are preserved after adding the assessment. The repository fixture retains exact scores, timestamps, weather values, grids' issue times and archive digests in `tests/fixtures/frost_nov19_atmosphere.json`.

| Lead | Opening logistic / forest | Weather | Midpoint logistic / forest | Weather |
| --- | --- | --- | --- | --- |
| H0 | 32.02 / 26.23 | Weak | 65.94 / 29.33 | Uncertain |
| H1 | 81.73 / 25.32 | Weak | 87.87 / 29.05 | Uncertain |
| H3 | 65.69 / 38.26 | Weak | 59.89 / 21.35 | Uncertain |
| H6 | 47.43 / 32.03 | Weak | 9.27 / 19.67 | Uncertain |

At opening the observed air/dew point was 46.0/42.1°F. H0/H1/H3/H6 forecast air was 47.4/45.9/43.3/42.7°F. Both NWS issues called for a 43°F low. At midpoint the observed air and dew point were both 37.0°F with calm wind and clear sky. Forecast air was still 41.2/40.9/40.8/40.2°F. This is evidence for possible localized cooling and forecast error. It cannot establish either a near-certain frost event or a confirmed frost-free night. Missing physical observations remain unknown. Original archives retain their contemporaneous scores.

Sustained 40, 45 and 50°F with a 10°F dew-point spread, 14 kt wind and overcast sky produced weak support at all four horizons for both replay times. Across the eight historical models, the opening warm-scenario scores ranged from 0.18–19.54%, 0.09–19.36% and 0.05–16.41%, respectively. At 50°F with higher humidity, light wind and overcast sky, the H3 logistic model still scored 48.19% while weather support remained weak. These controlled comparisons expose sensitivity to moisture and other features. They do not measure an observed frost-event false-alarm rate. Near-freezing clear/light-wind nighttime conditions can yield patchy frost potential. Subfreezing, ice-saturated air supports frost. Dry, windy, cloudy, conflicting and incomplete inputs have explicit regression cases.

## Offline improvement and release boundary

The existing records can support offline refits for treatment reasons. Atmospheric probability calibration needs an independently observed, time-aligned presence/absence series covering warm **and** cold nights irrespective of treatment selection. A verified untreated departure would need explicit whole-departure evidence and unambiguous identity and time. Even then, observing no treatment alone cannot establish frost absence. NeoSubZero test and pretreat records are excluded.

Pre-register the target, observation method, acceptable false-alarm/miss rates and uncertainty bounds before fitting. Hold out entire later seasons, group all aircraft and leads from a night together, and fit feature transformations within training folds only. Reserve separate nights for calibration. Compare each family and horizon with a seasonal baseline and with the weather assessment. Report warm-night false alarms, cold-event misses, coverage, calibration curves, Brier score, and night-block confidence intervals. Report disagreements and unavailable forecasts separately. Insufficient independent positive or negative nights preclude percentage claims. Promotion requires independent holdout results and explicit owner approval.

The change adds a versioned interpretation to new scheduled captures. Existing receipts lacking that interpretation show it as unavailable. Rollback consists of reverting the software release through AutoDeploy. Readers of the earlier schema ignore the additive payload fields. No archive rewrite or model replacement is needed.
