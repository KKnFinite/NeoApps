# FROST final preflight — November 18 evening Sort

Starting LIVE source: `24da5963b5d0fbf2ec9182b8def98339aa90aa78`.
Production checks are read-only. No forced capture, operational Sort creation,
training, setting changes or archive write probes were performed.

## Results and evidence

1. **PASS — model registration.** All eight READY research-model rows match every
   version and SHA-256 in the frozen prospective manifest: logistic/forest pairs
   for H0/H1/H3/H6. Source and manifest references are present. Authenticated
   retrieval of genuine capture 3764 passed the server's archive checksum check
   and returned eight scored predictions against the pinned frozen baseline.
2. **PASS — toggles.** RFD revision 3: predictions ON, live learning OFF, automatic
   retraining OFF. Confirmed by read-only database inventory and authenticated UI.
3. **PASS — independent scheduling.** Configured Monday–Thursday Night window is
   21:00–05:00 America/Chicago, yielding November 19 03:00 and 07:00 UTC (November
   18 21:00 and November 19 01:00 CST). The heartbeat initializes in Gunicorn's
   post-worker hook, independently of browsers/operational records. Production
   memory-diagnostic logs show recurring successful archive cycles through
   02:07:21 UTC and no worker ownership lease remains held. Service is LIVE and
   unsuspended, one existing worker; no new services or timers introduced.
4. **FAIL → corrected — DUCC without an operational Sort.** Independent synthetic
   reproduction found that `weather.current_sort` was None without an operation,
   hiding the hourly forecast/scores. Normal-view polling also inherited the
   no-current-Sort stop condition. DUCC now projects only the configured weather
   date/window using MotherBrain's existing read-only calendar resolver, including
   the preceding night after midnight. A transient weather namespace is never
   persisted as an operation. Empty DUCC uses its existing screen refresh setting
   independently of flight records, respecting configured OFF and cadence. Other
   staffing/callout refresh behavior is unchanged. No DUCC template/CSS/JS changes.
5. **PASS — resources and archive readiness.** At 02:10 UTC the measured container
   held 234.25 MiB of its 512 MiB limit: 277.75 MiB free and 117.75 MiB below the
   strict HRRR admission ceiling after reserving transfer headroom. Render memory
   metrics separately reported about 207 MiB (not the full cgroup accounting).
   CPU was quiet, one service instance, and no failure/restart events were found
   since this release. Existing archive credentials can read and checksum-verify
   a real prediction object; last verified capture was 09:15 UTC with 8/8 scored.
   Read-only preflight does not constitute a new archive write test or a guarantee
   of future memory headroom under busy operational traffic.

## Minimal correction verification

- Original no-operation regression failed with `current_sort is None` before fix.
- 18 focused Python cases passed: committed-score projection, configured overnight
  weather without an operation, no INSERT/UPDATE/DELETE on reads, existing weather,
  empty ramp geometry and explicit screen-OFF/callout-policy preservation.
- Chromium and WebKit: actual five-second configured polling; a read-only user
  opens DUCC with zero SortDateOperation rows; a synthetic committed capture arrives;
  all four horizon pairs appear without reload and operation count remains zero.
- Changes are limited to weather context, DUCC refresh selection, focused tests
  and this evidence note. No scheduler, model, security, treatment or UI changes.
- Local evidence: `instance/frost-preflight-20261118/` (ignored). Existing warnings
  were retained. The exact released SHA/deployment and final live verification are
  recorded in the completion report; no synthetic production result is claimed.

Tonight's actual two-cycle success remains to be observed at/after the configured
deadlines. Readiness checks do not establish overnight 8/8 coverage in advance.
