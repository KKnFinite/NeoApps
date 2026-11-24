# RFD Night FROST prediction replay, November 19–20, 2026

The latest committed RFD captures available on November 23 were 3885 and 3889. The opening H6 logistic score really was 99.04%. The forest scored 30.15% on the same inputs. The parser discarded clear-sky observations and used the nominal reporting hour. Both errors change frozen-model inputs. A separate limitation remains even with correct inputs. The label used to fit all eight models is a FROST-coded reason among recorded departure treatments. The training evidence cannot establish a probability of atmospheric frost.

## Archive verification

The production baseline was `819f581105d88559aa9aa6978bc28cfbc0d1458a`. Both original archives matched their committed canonical SHA-256. All eight local artifact files matched the registry and both archives. Rebuilding all 16 original vectors from the archived observations and eligible HRRR forecasts reproduced the contract, missingness, provenance and values (largest floating-point difference about 1.1 × 10⁻¹⁶). The production scorer and independent logistic coefficient or forest leaf arithmetic reproduced all 16 scores within 10⁻¹³. All four horizons used the correct cycle, target hour and availability evidence. The point-series hashes, raw archived METAR response and the conversion from Kelvin into Celsius were verified. HRRR wind uses knots after conversion from m/s. The archived exact HRRR issue instants are unknown. Original NOAA object availability timestamps establish that the products existed before each cutoff.

| Capture | Cutoff UTC on Nov 20 | HRRR cycle UTC | Commit UTC | Archive SHA-256 |
|---|---|---|---|---|
| 3885, Thu Nov 19 21:00 CST | 03:00:33.270651 | 01:00 | 03:00:35.292598 | `164d1c21cf383c0b16d9ba5f24709e2b32704b7166e12833ac0be86308a380df` |
| 3889, Fri Nov 20 01:29 CST | 07:29:30.495999 | 05:00 | 07:29:32.326881 | `40cce8a2a9867d869629b6767a537530dee414f6f159f38c8efe27dfe24a0dc3` |

Each exact prediction target is its cutoff plus the stated lead. HRRR samples are hourly. The midpoint forecasts were valid 29 minutes and 30.496 seconds before their respective targets. The final midpoint H6 target fell after Sort close. A committed score alone does not prove that particular hour was shown on a DUCC screen. The opening H6 target fell inside the Sort.

## Inputs and frozen-score replay

Each score cell gives **archived → corrected replay**, in percent. The original archives retain their original scores. Weather is the HRRR input used for that horizon. DUCC can also show temperatures from a separate NWS hourly product.

| Capture / lead | HRRR valid UTC | Air / dew °F | RH % | Wind / gust kt | Cloud % | Logistic | Random forest |
|---|---:|---:|---:|---:|---:|---:|---:|
| Opening H0 | 03:00 | 47.36 / 45.35 | 92.69 | 4.91 / 11.86 | 1 | 87.63 → 32.02 | 26.23 → 26.23 |
| Opening H1 | 04:00 | 45.86 / 44.07 | 93.44 | 4.35 / 10.81 | 2 | 21.70 → 81.73 | 25.32 → 25.32 |
| Opening H3 | 06:00 | 43.27 / 41.31 | 92.75 | 2.89 / 8.98 | 2 | 11.79 → 65.70 | 38.26 → 38.26 |
| Opening H6 | 09:00 | 42.69 / 40.98 | 93.62 | 5.07 / 5.06 | 27 | 99.04 → 47.43 | 30.15 → 32.03 |
| Midpoint H0 | 07:00 | 41.16 / 38.42 | 89.90 | 3.91 / 5.12 | 0 | 96.97 → 65.94 | 26.25 → 29.33 |
| Midpoint H1 | 08:00 | 40.85 / 38.32 | 90.60 | 3.74 / 3.76 | 1 | 15.95 → 87.87 | 27.16 → 29.05 |
| Midpoint H3 | 10:00 | 40.75 / 38.17 | 90.42 | 4.89 / 4.84 | 99 | 9.44 → 59.89 | 22.11 → 21.35 |
| Midpoint H6 | 13:00 | 40.18 / 38.81 | 94.82 | 4.83 / 7.46 | 92 | 97.78 → 9.27 | 19.67 → 19.67 |

KRFD observed 46.04°F, dew point 42.08°F, RH 85.98%, wind 150° at 3 kt and clear sky at 02:54 UTC. At 06:54 UTC, KRFD observed 37.04°F, dew point 37.04°F, RH 100%, calm and clear. The night therefore included actual cooling and saturation. It is a different case from a hypothetical steady 50°F overcast night. Physical frost occurrence for this Sort is unknown. [NWS explanation of frost and surface cooling](https://www.weather.gov/arx/why_frost)

## Causes and correction

* **Observation coverage.** AviationWeather returned `cover: CLR` with `clouds: []`, consistent with the original coded METAR. The normalizer previously looked only at layers and recorded unknown sky. This affected 9 of 14 reports at opening and 12 of 14 at midpoint. The feature measures hours with known sky observations, irrespective of cloudiness. Opening coverage changed from 3.000 to 11.109 hours and midpoint from 1.000 to 11.592 hours. For H6 logistic this changed the coverage contribution from +5.052 to +0.255 log-odds at opening and +6.235 to −0.031 at midpoint. With only sky repaired, the H6 scores are 47.52% and 8.16%. H1 and H3 learned coefficients with the opposite sign for this feature, which explains their increases.
* **Observation time.** The source explicitly gave observation times corresponding to the coded 02:54Z and 06:54Z METARs. The normalizer had used the 03:00 and 07:00 nominal hours. The correction prefers the supplied observation instant, with the previous fallback for older responses. Retrieval cutoff and forecast availability checks still apply. Detailed layers retain precedence over summary sky. Unknown sky, absent ceiling height and censored visibility remain missing where an exact value is unavailable. [Aviation Weather Center METAR documentation](https://aviationweather.gov/help/data/)
* **Display.** The hourly weather previously labeled the pair simply “FROST”. DUCC now identifies the recorded treatment reason and explains the meaning and order of the two research scores. An archived score cannot establish that an aircraft was treated or that atmospheric frost formed.

## Controlled meteorological checks

All eight unchanged artifacts were exercised at both actual capture times. Each scenario changes the entire observation history and every forecast hour, including dew point, humidity, wind, cloud and weather indicators. The three warm scenarios hold air temperature constant, dew point 10°F lower, overcast skies, 14 kt wind and zero precipitation. The frost-supporting comparison holds 29°F air, 28°F dew point, clear sky and 2 kt wind. The table shows **logistic / forest** percentages at the opening cutoff. These are sensitivity checks with no assigned outcome.

| Scenario | H0 | H1 | H3 | H6 |
|---|---:|---:|---:|---:|
| 40°F | 0.18 / 16.46 | 1.89 / 12.75 | 5.56 / 19.54 | 5.38 / 14.87 |
| 45°F | 0.09 / 12.23 | 1.04 / 11.01 | 3.67 / 19.36 | 3.87 / 14.73 |
| 50°F | 0.05 / 12.23 | 0.57 / 10.56 | 2.40 / 16.41 | 2.78 / 15.02 |
| 50°F, overcast, 49°F dew point, 2 kt | 1.71 / 22.93 | 20.32 / 19.15 | 48.19 / 25.95 | 28.27 / 30.41 |
| 29°F, clear and humid | 38.33 / 62.72 | 90.96 / 63.27 | 93.62 / 55.46 | 85.02 / 50.88 |

No tested sustained warm scenario produced a near-certain score. Material scores remain even with steady 50°F temperatures. Adjusting a warning threshold or fitting a calibrator to these same treatment labels cannot turn these results into reliable atmospheric probabilities.

## Offline model disposition

The hash-verified historical dataset (`c5760b5e99389eeb5fec79105b755047e1fd6215632cba3f96437ea728ef7b61`) contains 280 eligible examples per H0/H1/H3 and 277 for H6. Each horizon has 83 FROST-reason examples, including one with forecast temperature at least 45°F. It contains **zero confirmed physical positives and zero credible physical negatives**. The prior forward-season evaluation of treatment reasons yielded 20–23 logistic false alarms and 4–10 forest false alarms per horizon on 139 other-treatment nights. Those counts describe mistakes in predicting recorded reasons. Physical false-alarm rates, recall and calibration are unmeasurable with these labels.

1. Freeze this replay, all source hashes and the eight artifacts. Rebuild historical input parity offline, checking observed time, coverage, censored visibility, cutoff availability and differences between treatment-time sampling and scheduled opening/midpoint inference.
2. Review only authorized Cryotech/Xchange originals for **independent, time- and location-matched evidence** of atmospheric frost present or absent, with explicit outcome criteria. Keep treatment choice, no-treatment records and unknown outcomes separate. If the originals cannot supply both outcome classes across representative treated and untreated nights, stop. Atmospheric-risk training and validation require separately authorized independent observations.
3. If the label gate passes, refit the same four logistic and four forest horizons offline. Keep entire nights and seasons together, hold later seasons out, fit any calibrator on a separate earlier validation period, and compare against weather-only and prevalence baselines. Evaluate warm stable and radiatively cooling strata separately, report sample sizes, reliability curves, Brier scores, misses and false alarms with night-level uncertainty. Examine sensitivity to observation coverage and acquisition time. Establish acceptance limits before viewing holdout results.
4. Retain the frozen artifacts until independent validation and explicit promotion approval. No live Sort training or changes to historical receipts are required for the parser correction.
