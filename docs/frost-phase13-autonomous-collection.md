# FROST Phase 13 autonomous collection

Starting main and LIVE: `50b89b2041121e920d4d1792cb8965412f740394`.

NeoApps collects current KRFD weather evidence on its existing Render delivery heartbeat. Neither an open browser, an active SubZero sort, a recent CSV nor an online workstation is required. Actual treatment evidence arrives through later authenticated Xchange CSV imports. Historical extraction, fitting and substantial evaluation remain LOCAL.

## Scheduling and resource limits

`run.py` already starts `initialize_delivery_heartbeat(app)`: first wake after ten seconds, then every sixty seconds. The gateway list now includes canonical RFD even when there are no documents. The existing outbox thread runs one due capture and one small outcome page; no additional timer, service, paid cron or hosting plan is introduced. `FROST_AUTO_COLLECTION` defaults on for RFD and can disable collection without changing historical evidence.

`frost-auto-v1` uses UTC hour identities and the existing PostgreSQL task uniqueness, row locks and fenced leases. The shared FROST memory-budget lease serializes capture/matching across processes. Acquisition has three attempts per slot with backoff. Staged deliveries retry with backoff without reacquiring or backdating their source. At most two snapshots await delivery; later slots explicitly become missed rather than growing an unlimited staging backlog. A restart recovers expired leases and retains already committed source bytes. Expired empty slots are never reconstructed as forecasts.

This is best-effort hourly collection inside the existing always-on service, not an independent scheduling service or an uptime guarantee. Outages, provider failures, resource suspension, and a busy existing outbox can delay or miss slots. The authenticated AUTO COLLECTION panel distinguishes configured collection from recent off-site-verified collection and reports missed/failed intervals.

The existing container-wide 75% / minimum 128 MiB headroom gate remains. Public requests have ten-second timeouts and individual 128/64/512 KiB bounds; an uncompressed snapshot is limited to 1 MiB and compressed staging to 256 KiB. Network responses and R2 clients close on all exits. No training/scientific/geospatial library or historical dataset is loaded for capture. Existing opt-in memory diagnostics include `frost_auto_capture`.

## Sources and frozen models

The collector retrieves AviationWeather.gov KRFD METAR/SPECI for the preceding fourteen hours and NWS points/grid data for 42.19151, -89.08760. It verifies station/grid location, times and units using existing contracts. Original bytes, hashes, retrieval times, observation times, NWS update/valid times and normalized values remain distinct. A partial NWS outage retains useful fresh METAR evidence with an explicit source error.

Full HRRR extraction still requires LOCAL scientific/geospatial tooling. NWS grids do not satisfy `krfd-forecast-native-v1`; substituting them would invalidate the frozen models. Autonomous native scores therefore remain **UNAVAILABLE** until safely obtainable compatible inputs exist. This release does not claim autonomous native-model warning skill.

Authorized administrators may upload exact frozen JSON files under REGISTER EXACT FROZEN RESEARCH JSON. Only the eight Phase 12 SHA-256 values in `app/data/frost_prospective_research.json` are accepted. The baseline fingerprint remains `b8f33680a91ae65b650b69b2c0872ed024356d3c741651e2e52d4a62bbafb783`; thresholds remain 0.5. Contract checks and numerical self-tests precede immutable private Vault registration. These records never enter the operational model or approval tables. Synthetic compatible-input tests exercise pure-Python scoring; missing, future or incompatible inputs cannot produce scores.

## Durable receipts and later Xchange outcomes

Snapshots first commit to a bounded database staging record. A separate receipt timestamp is taken after that commit; recovery uses the later recovery time, never a reconstructed earlier commit. R2 immutable content-addressed writes and checksum read-back must succeed before a snapshot becomes ready. Canonical SQLite/PostgreSQL bootstrap adds guards against modifying or deleting verified capture/model receipts; there are no new tables or columns.

During normal original-document delivery, prospective-era source rows are compacted into encrypted pages of at most 100 records. Immutable off-site pages retain original document hash, source row, Xchange RecordID, raw-row hash, modification evidence and canonical Central-calendar START times. Existing original CSV encryption, parser, duplicates, provenance and archive receipts remain intact. Older seasons are not backfilled into this index.

Matching runs one due page at a time. It uses actual spray START, not CSV upload time. For each 0/1/3/6-hour horizon, a verified snapshot must have committed before the treatment, with a target no more than sixty minutes before it. The latest eligible target is selected without reference to score or outcome correctness. RecordID/revision contracts deduplicate overlaps; uncertain conflicting revisions supersede active links and require review. Immutable pages and original exports preserve all competing evidence.

A forecast link with an unavailable score is evidence coverage, not model validation. FROST departure treatment remains likely-positive reason evidence; pretreat remains anticipated risk; other reasons are not physical negatives. Missing rows create no outcomes. No live SubZero spray records are created or changed.

## Local research refresh

The existing authenticated HTTPS client prompts for a password without saving it. Verified cached objects are reused; catalogs retain immutable versions. No unrestricted production database credential is needed.

```powershell
.venv/Scripts/python.exe -m scripts.frost_prospective --local refresh-auto --origin https://neoapps.onrender.com --email YOUR_AUTHORIZED_EMAIL --output instance/frost-local/phase13/evidence
.venv/Scripts/python.exe -m scripts.frost_prospective --local assemble-auto --baseline instance/frost-local/phase12/frozen/baseline.json --evidence instance/frost-local/phase13/evidence --output instance/frost-local/phase13/analysis
.venv/Scripts/python.exe -m scripts.frost_prospective --local evaluate --dataset instance/frost-local/phase13/analysis/dataset/dataset.json --output instance/frost-local/phase13/evaluation
```

The adapter verifies original source bytes/hashes and capture times, deduplicates all Xchange page revisions locally, and reuses the Phase 12 tail/night/horizon/cohort evaluator. It preserves original autonomous objects and derives separate adapters. Native SCORED captures require LOCAL HRRR source-proof reconciliation; the adapter refuses unsupported scored input rather than pretending verification succeeded. Unknown outcomes and unavailable predictions stay explicit.

The historical treatment archive lacks a representative untreated-aircraft population. This pipeline can support conditional treatment-reason evaluation, not calibrated physical frost probability. Optional inspections remain useful but do not gate collection, FIN, CLEAR or departure. No model is approved, activated, retrained or shown as a DUCC warning.

## Verification

Focused tests exercise SQLite and disposable loopback PostgreSQL, real separate OS processes, a real heartbeat timer with no browser/document dependency, safe retry/restart, memory deferral, immutable populated-schema bootstrap, exact research upload authorization, late CSV matching, overlapping revisions, and authenticated incremental downloads. Chromium/WebKit cover 1920×900, 1920×1080, 1366×768, 3840×2160, tablet and 320–430px layouts, rejected-upload file retention, research registration and existing native CSV import. Production verification and observed capture intervals are reported separately from isolated proofs.
