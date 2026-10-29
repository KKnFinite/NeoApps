# FROST Phase 17 — operational priority and private prediction history

Research remains experimental treatment-reason classification. No model,
threshold, feature contract, approval, operational warning, mission authority,
employee recovery rule, or hosting configuration changes in this release.

## Resource policy

The existing single non-preloaded Gunicorn worker and delivery heartbeat remain.
There is no additional production thread, process, scheduler, or polling system.

`frost_resources` counts active non-static/non-health requests in the web worker.
Optional capture/research yields while a request is executing. A request taking
at least one second gives operational responses a two-second recovery interval.
Linux cgroup CPU PSI `some avg10 >= 20%` also defers research; PSI is sampled at
most once per second on demand. This conservative admission threshold measures
CPU waiting, not CPU utilization or a user-capacity rating. Missing PSI does not
disable the existing fail-closed memory guard.

Existing memory limits are unchanged: <=75% charged container memory, >=128 MiB
headroom, plus the HRRR 32 MiB reserve (352 MiB admission on the 512 MiB plan).
HRRR rechecks before requests, every <=64 KiB network read and each <=1 MiB
decoded block. Scoring checks before each artifact and again before inference.
Private snapshot delivery and optional departure/Xchange correlation yield too.
Provider/storage clients retain their existing bounded lifetime and cleanup.
Read transactions are released before weather/model/snapshot network I/O.

This is cooperative admission, not a memory reservation or hard preemption of
an in-flight socket call. An arriving request can allocate memory between checks.
Timeouts/download bounds remain essential. Request activity is worker-local;
container PSI/memory and PostgreSQL fenced capture leases are cross-process.
Do not increase the worker count and assume this is a global request counter.

Resource contention retries on the existing heartbeat without spending the
three provider-failure attempts. Expired uncommitted slots remain MISSED; staged
evidence retains its original commit time and is never reacquired/backdated.
The existing two-staged-slot ceiling is unchanged.

## Authenticated history

The FROST page lists up to 24 recent capture index rows without fetching R2.
An explicit VIEW action uses the existing authorized, bounded, hash-verified
private evidence endpoint. It displays actual observations, HRRR cycle and valid
times, H0/H1/H3/H6 logistic/forest scores or UNAVAILABLE, model hashes, input
quality, and outcome-link counts. H0 is current recognition, not advance warning.
Missing outcomes stay UNKNOWN. Failed reads preserve the previous view. Refresh
the page to refresh the recent-slot index; no new background browser poll exists.

## Reproducible isolated load probe

Run only on a local Linux/systemd environment with the repository dependencies
and existing ignored Phase 12/14 frozen artifacts/parity source cache:

```sh
python -m tests.frost_load_probe --directory instance/phase17-load-baseline --seconds 30 --start
python -m tests.frost_load_probe --directory instance/phase17-load-priority --seconds 30 --start --priority --writes
```

`--start` requires local systemd service permission. It starts a transient
loopback-only service with MemoryMax=512 MiB, CPUQuota=50%, one sync Gunicorn
worker, no preload, and a 15-minute maximum lifetime. Client traffic runs outside
the server cgroup. The temporary SQLite database has 40 tails/80 canonical
missions, five ramps, 80 synthetic staffing people, and 20 authenticated users.
No production URL or credentials are accepted. Remote providers are blocked.
Nine real operational routes are exercised at 5/10/20 concurrent users with
250 ms think time. Optional writes exercise CSRF/version-protected NeoRain
delay add/update/delete on synthetic inbound/outbound missions only.

The background replay verifies cached HRRR source hashes and decoder parity,
then the eight frozen artifact hashes/numerical self-tests. It simulates 10 ms
per provider request and verifies a bounded local archive checksum. This is NOT
real R2 latency, an actual hourly heartbeat, or fresh forecast acquisition.
Those paths are tested separately with PostgreSQL/process fixtures and observed
production receipts. Linux cgroup PSI is read from the test service rather than
the host root. Reports retain latency, failures, cgroup memory/CPU and RSS/PSS.

### Observed read-only results (2026-10-29)

Each block lasted 30 seconds. All listed requests succeeded. Latencies are ms.

| Users | Baseline idle p95 | Baseline replay p95 | Priority idle p95 | Priority replay p95 |
| --- | ---: | ---: | ---: | ---: |
| 5 | 139 | 139 | 177 | 141 |
| 10 | 313 | 338 | 301 | 329 |
| 20 | 748 | 827 | 811 | 751 |

Baseline: 3,832 requests, 13 complete replay cycles; peak charged memory
180.8 MiB. Priority: 3,836 requests; 29 replay deferrals (25 active requests,
4 CPU pressure); peak 179.3 MiB. Mean CPU was approximately 0.45 CPU in both,
close to the 0.5 allocation. These short samples show protective yielding,
not a statistically established speedup. At 20 users the worker was already
CPU-constrained without FROST.

The final mixed read/write run (`instance/phase17-mixed-final`) completed 4,345
requests, including 726 successful CSRF/version-protected delay mutations, with
zero failures. Idle/active p95 values were 157/168 ms (5 users), 314/341 ms
(10), and 695/817 ms (20). All 29 research attempts during traffic yielded;
three more yielded while CPU PSI decayed. Four complete parity/scoring replays
then succeeded in the 30-second quiet recovery period (1.09–1.21 s each).
Peak charged memory was 182.6 MiB, final 182.4 MiB. Because no full research
cycle ran during this traffic, the latency differences cannot be represented
as measured scoring cost or proof of an improvement. Request latency still
varies under the constrained worker. No OOM, worker timeout or HTTP error was
observed in these isolated runs.

The longer run (`instance/phase17-soak`, `--seconds 10 --priority --writes
--soak-seconds 600`) then measured 616.7 elapsed seconds of light authenticated
traffic with repeated verified replays. All 199 soak requests succeeded:
p50/p95/p99 = 18/57/179 ms, maximum 197 ms. Across preparation and soak there
were 101 complete replays and 58 cooperative deferrals. Soak cgroup memory
started at 181.5 MiB, peaked at 183.3 MiB, and ended at 176.5 MiB; worker PSS
increased only 1.04 MiB (164.74 to 165.79 MiB). No OOM counters incremented.
This window did not show unbounded replay retention. It does not exercise real
R2 SDK/network retention or establish multi-day production stability.

The longer run's separate 10-second **idle preparation** block had an 11.46 s
p95 latency burst at 20 users before FROST replay started. Its cause was not
established; concurrent workstation/browser tests and this synthetic environment
are possible confounders, not proven explanations. It is retained in the report
and is another reason not to make an unconditional capacity/latency claim.

Limitations: Linux Python 3.12 versus Render 3.14; SQLite rather than a remote
PostgreSQL workload; no external provider latency or full production data/SDK
footprint. Local memory is lower than production. PostgreSQL authorization,
concurrency, leases, corrections and retry tests are separate. This is neither
a full production-equivalent benchmark nor a real-world user-capacity guarantee.
Invalid early fixture runs (stale login/CSRF setup) are excluded from metrics.

## Production audit before deployment

At 08:20 UTC on October 29, the existing release had 11 verified hourly
captures, one missed slot, eight scores and 80 unavailable model results. The
07:00 UTC natural slot scored all eight models; the 08:00 slot retained verified
METAR/NWS evidence but HRRR was unavailable under the unchanged memory guard.
Eight frozen registrations remained; no operational model was approved. The
11-capture figure is cumulative across releases, not 11 new Phase 16 captures;
the two naturally observed 07:00/08:00 slots are the current-release evidence.

Sampled charged cgroup memory rose from roughly 342 MiB at 07:54 to 348–349 MiB
around 08:00–08:16. At 08:15, parent PSS was 21.5 MiB and worker PSS 292.6 MiB;
anonymous memory was 304.8 MiB, file cache 21.9 MiB and kernel memory 21.5 MiB.
This leaves narrow HRRR headroom and does not demonstrate long-term stability.
No retained-client leak was established, so no speculative allocator/GC or
worker-restart changes were made.

Baseline log exceptions were not hidden: before this release, at 06:57:59 UTC
the Google MotherBrain live poll returned HTTP 500 with
`GoogleMotherBrainReaderError`; at 07:18:16 the NeoRain recap fallback logged a
Google Sheets 403 permission error while its page returned HTTP 200. These
external integration failures predate this change and are not repaired here.

Departure discovery continued independently: 176 qualified and 1,102 requiring
review at the audit checkpoint; 205 archived revisions, 1,091 pending. The
review group comprised 800 unverified-tail rows and 302 unsupported `alp`
actual-time sources (one also lacking verified tail). These are indexed rows,
not an assertion of complete historical discovery. No Xchange outcome links,
physical inspection population, or credible no-spray population were present.
All unmatched departures remain UNKNOWN. No MotherBrain records were changed.

## Safety verification

Focused tests cover active-request admission, mid-transfer/decode interruption,
CPU/recovery deferral, no open transaction at weather acquisition, fenced
cross-process captures, immutable retries, frozen model hashes, tail corrections,
late overlapping exports, recovery expiry/reattachment and unauthorized access.
Actual Linux Gunicorn preload/non-preload/two-worker/restart lifecycle tests
verify recovery ownership (the production deployment remains non-preloaded).
Chromium/WebKit private history checks include 1920x900, 1920x1080, 1366x768,
3840x2160, tablet and 320/375/390/430-pixel mobile layouts, failed-read recovery
and permission denial. Existing CSV/model JavaScript tests remain unchanged.

Completed test runs (some suites inherit overlapping cases; do not sum these
as unique tests):

- 159 focused Phase 13/14/15/17, employee worker and NeoRain PostgreSQL tests.
- 50 priority, real-process capture and recovery-service tests.
- 35 memory diagnostics, archive-client/worker and process-safety tests.
- One actual Linux Gunicorn lifecycle test, covering two workers and restart.
- Seven browser test methods across Chromium/WebKit: prediction history,
  mobile CSV upload/recovery, employee restoration, Rain CRUD, and SubZero
  layout/preferences/treatment forms/draft focus/tail identity.
- 15 existing CSV/model JavaScript tests, new-script syntax, Python compilation
  and `git diff --check`.

Isolated PostgreSQL uses `NEOSTAFFING_TEST_POSTGRES_URL` with a disposable
loopback database and unique schemas. The Windows-hosted PostgreSQL listener
was not reachable from the capped WSL namespace, so the load measurement is
explicitly SQLite, not a PostgreSQL performance claim.

Long-term memory/coverage, genuine physical-frost accuracy and independent
prospective evaluation remain unproven. Missing treatment is never a negative.
Heavy research remains local; no models are trained or approved by this release.
