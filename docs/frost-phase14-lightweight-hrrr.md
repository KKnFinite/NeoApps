# FROST Phase 14 lightweight HRRR research scoring

Starting main and LIVE: `f95693ff47cd870aabcd24ddef65743cbadd205e`.

The existing hourly heartbeat can acquire the exact HRRR-derived KRFD inputs used by the eight frozen Phase 12 research models. Models remain private research artifacts, not operationally approved forecasts. Their target is frost reason among recorded departure treatments, not physical aircraft frost probability.

## Source and compatibility

The reader uses the same University of Utah NOAA-derived HRRR Zarr archive as the LOCAL research extractor. It requests only the tile containing verified cell x1130/y677, checks neighboring projected coordinates, grid dimensions, units, cycle/reference time and every valid hour, and reads individual point values from bounded compression blocks. The cell is 42.20308995815746, -89.09953425960873, approximately 1.62 km from KRFD. The established Lambert wind rotation is unchanged.

NOAA original GRIB-object HEAD receipts establish a conservative available-by bound. They are not exact issuance or operator receipt times. The collector selects one run two hours before the current UTC hour, checks original availability before the cutoff, and retains retrieval time separately. It never substitutes NWS values, future observations or a nearby airport. Missing source fields remain null under the frozen model's existing missingness contract.

`krfd-forecast-native-v1` remains unchanged. The new transport/decoder is `krfd-hrrr-light-v1`. Exact normalized-field parity passed for eight hours in each of these cycles: 2020-10-01 23Z, 2025-12-01 00Z and 2026-10-28 21Z. The older cycle includes genuinely absent archive variables and float16 values. The 2025 cycle already has original NOAA GRIB/ecCodes grid and packing verification in the existing local evidence. Point-coordinate tolerance is 1e-8; normalized scalar comparisons require exact equality.

NOAA NOMADS geographic/parameter subsetting was considered, but returns GRIB that still needs a compatible decoder. Reusing the already verified chunk archive avoids introducing a new provider or changing frozen feature semantics. No nationwide forecast file is downloaded.

Sources: [Utah archive structure](https://mesowest.utah.edu/html/hrrr/zarr_documentation/html/zarr_variables.html), [archive access](https://mesowest.utah.edu/html/hrrr/zarr_documentation/html/zarr_HowToDownload.html), [NOAA GRIB filter](https://nomads.ncep.noaa.gov/info.php?page=gribfilter), [Blosc chunk format](https://github.com/Blosc/c-blosc/blob/main/README_CHUNK_FORMAT.rst).

## Resource limits and evidence

Only the small LZ4 block codec is added. Production imports no NumPy, pandas, scikit-learn, pyproj, ecCodes or numcodecs. The reader understands the source's Blosc1/LZ4 byte shuffle and rejects unsupported compression. It decodes at most one block of at most 1 MiB at a time; it never constructs the forecast grid array. Metadata is bounded to 64 KiB, coordinate/time arrays to 32 KiB, each compressed tile to 5 MiB, and total transfers to 18 MiB. A capture has at most 80 provider requests, five-second request timeouts and a 90-second overall budget. There is no hidden provider retry loop or search through old cycles.

The existing 75 percent container / 128 MiB minimum headroom protection remains. HRRR additionally reserves 32 MiB below those limits before requests and archival transfers. Insufficient resources preserve METAR/NWS capture and produce an explicit native UNAVAILABLE reason. `FROST_AUTO_HRRR` can disable only this optional reader; it does not disable collection. No worker, plan or hosting subscription is added.

Original source chunks are immutable, content-addressed private FROST Vault objects with verified read-back. Hourly snapshots contain compact receipts and the normalized point, not all raw tile bodies. Acquisition and eight model reads share one closed storage-client scope; cyclic SDK objects are reclaimed before snapshot delivery. UTC-slot leases, durable staging, conservative post-commit timestamps and restart/retry rules are unchanged.

A fresh Windows process acquired a real current run in 25.13 seconds using 75 requests and 5,178,322 downloaded bytes. Sampled RSS rose from 111,075,328 to a peak of 130,375,680 bytes, ending at 121,012,224. No scientific modules loaded. These are local process measurements, not Render container measurements; live container observations must be reported separately.

## Exact frozen model registration

Run from the workstation repository:

```powershell
.venv\Scripts\python.exe -m scripts.frost_prospective --local register-models --origin https://neoapps.onrender.com --email YOUR_LOGIN_EMAIL --baseline instance/frost-local/phase12/frozen/baseline.json
```

The existing HTTPS client prompts privately for the password, obtains CSRF, and saves no credentials. All eight local files, their original evaluation receipts, hashes, contracts and numerical self-tests are checked before the first upload. The existing authorized multipart endpoint performs its own exact hash checks and memory guard, private immutable storage, and idempotent registration. A failed request stops the command; rerunning retains prior successful registrations. No browser filesystem permission, production database credential, model training or approval is required.

Registration is not activation. The baseline hash, model hashes and fixed 0.5 thresholds remain unchanged. Any unavailable artifact or incompatible inputs produce UNAVAILABLE rather than an invented score. No DUCC warning or operational model release is created.

## Local verification and later outcomes

The authenticated `refresh-auto` command retrieves new source bodies only, verifies hashes, and retains cache objects. The new gateway-scoped source-download route permits only receipts referenced by an already verified capture. `assemble-auto` re-decodes original HRRR bytes, verifies availability, reconstructs features and checks frozen scores before using the existing prospective matching policy.

Late Xchange imports still match actual Central-calendar application START, not import time. Overlapping RecordIDs/revisions follow the existing policy. Captures committed after an event cannot become advance-warning evidence. Missing treatments remain UNKNOWN. No live spray transaction is created. Model fitting and substantive evaluation remain LOCAL.

## Validation and release evidence

Focused tests cover scalar precision, shuffle/block handling, malicious sizes, unsupported codecs, missingness, source hashes, timing, memory reserve, exact registrations, authenticated source retrieval and scored local reconciliation. Existing SQLite/PostgreSQL lease, retry, late-Xchange, approval and browser regressions remain applicable. Private parity and probe receipts are under ignored `instance/frost-local/phase14`; historical parity receipts also remain beside their existing source caches. The isolated authenticated AUTO browser test passes Chromium/WebKit and the required desktop/mobile widths. Combining inherited browser classes can reuse source uploads and produce duplicate-upload 409s; run original CSV suites in a fresh process when diagnosing that fixture interaction.

Deployment status, actual registered-model count, live capture results and live memory must be recorded from production after AutoDeploy. Isolated scoring tests alone do not establish autonomous production scoring.
