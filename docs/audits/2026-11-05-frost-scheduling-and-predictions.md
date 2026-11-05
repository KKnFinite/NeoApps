# FROST scheduling and prediction correction

Starting SHA: `b34443943514e1f4fe0ecb5cab7ccb31f8226e04`.

## Confirmed defects

The hourly collector and deadline planner ignored Sort settings and acquired
weather around the clock. Production's canonical RFD settings specify Night
21:00–05:00 America/Chicago, Monday–Thursday starting nights. The separate
Operations window ends at 04:00; it must not shorten the configured Sort window.
Operating days come from `gateway_sort_matrix`, not API participation settings.

Read-only November 4 03:00–11:00 UTC receipt inspection confirms eight captures,
with all eight models scored only at 03:00. The remaining seven archived eight
UNAVAILABLE results: five encountered operational-request contention, two HRRR
memory/transfer protection. These temporary resource exceptions were swallowed
as ordinary missing-provider data, committing an immutable unavailable capture
that could not retry scoring during its hour. This is distinct from actual
missing/invalid forecast evidence, which must still remain UNAVAILABLE.

DUCC also presented hardcoded weather-point LOW/MEDIUM/HIGH levels and trends.
Those values were unrelated to the frozen historical-trained model outputs.

## Corrections

* One read-only schedule implementation serves collector, planner and status.
  It reads configured Sort start/end, active gateway weekdays and the established
  gateway timezone. No schedule times/days are hardcoded. Missing configuration
  fails closed. Overnight windows belong to their starting operating day;
  distinct UTC hour identities remain safe across DST.
* The existing deadline worker wakes for eligible hours/openings. Capture admission
  and post-acquisition/post-scoring clock checks prevent outside-window or expired
  hourly publication. No operational Sort/mission is created to run research.
  Missed-interval accounting excludes closed hours; existing history is preserved.
* Temporary HRRR/NWS/scoring resource deferrals propagate to the existing bounded,
  lease-fenced retry path instead of committing unavailable scores. Contention
  does not consume provider-failure attempts. An expired hour is never replayed.
  A successful immutable capture remains unique per UTC slot. Acquisition attempts
  can retry after partial failure; completed archive retries never reacquire or rescore.
* Already staged evidence can finish archival outside Sort hours, retaining original
  capture/commit times, checksums and scores. No extra current-hour row is created
  merely to deliver older staged evidence.
* HRRR releases transfer chunk copies before private archival and the previous
  compressed tile before loading the next variable. Closed SDK cycles are reclaimed
  before acquisition. Container guards remain 75% / 128 MiB headroom, plus HRRR's
  existing additional 32 MiB reserve; operational requests retain priority.
* Weather compatibility fields now return UNAVAILABLE/null, never heuristic risk
  classes/trends. DUCC no longer renders that risk column or its WHY control.
  Actual weather remains visible; saved preferences are retained. The research
  view continues to show genuine experimental H0/H1/H3/H6 logistic/forest scores,
  explicit unavailable reasons and the treatment-reason/physical-frost distinction.

## Verification

35 focused Python IDs passed, with six passing subtests reported separately.
Coverage includes configured weekdays/window changes, no configuration, foreign
gateway, overnight close, weekend next opening, DST, one capture per hour,
resource retry recovery, expired in-flight acquisition, after-hours archive retry,
the actual eight frozen scoring contracts, source-byte redecoding/provenance,
memory admission and no scientific-library imports in the runtime. Weather/UCC
rendering tests verify that even a legacy HIGH/rising payload cannot become an
operational frost alert. Existing hourly deadline and archive safeguards pass.
The new fixed-clock receipt fixture was corrected to use its test clock for
database defaults; no production guard was weakened.

A bounded 3 MiB mocked transport check verifies less than 1.5 body copies retained
at archival entry, protecting against the former joined-body plus chunk-list
retention. This is isolated allocation evidence, not a production memory-saving
or cost claim. Compilation and diff checks pass. No broad suite, load test or
browser workflow was run; no schema, hosting, worker-count or memory setting changes.

All eight production research-model registrations were ready on inspection.
Their model/version/hash contracts, frozen data/artifacts, research history,
training pipelines and promotion settings are unchanged. No automatic retraining,
pre-launch testing-data learning, operational alerts or historical repairs are added.
Unrelated files, jobs and synthetic database directories remain preserved.

## Release and limitations

Release is the main commit containing this report via existing Render AutoDeploy.
Exact LIVE SHA/deployment and startup, health/login and authorized read-only FROST
status checks are recorded in the completion receipt/final response after deployment.
Production diagnostics use SELECTs/GETs only; no synthetic operational writes or
manual weather/scoring trigger is used.

Resource headroom, source publication, complete observation windows and verified
frozen artifacts still determine score availability. No promise of eight scored
hours or improved prediction accuracy is made without subsequent prospective
production evidence. Scores remain experimental treatment-reason research,
not verified physical frost presence. Existing historical unavailable snapshots
are retained, never retroactively scored. Local logs: `instance/frost-scheduling-*.log`.
