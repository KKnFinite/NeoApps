# NeoApps pre-Sort service-egress investigation — November 5, 2026

Started **20:11 CST**, before tonight's 21:00 Sort. Main and LIVE starting SHA: `f5ac9cef662f33d3176b660a8a2991f277dfc479`, deployment `dep-dbmijqrbc2fs73ddkd00`. Latest origin/main was fetched and unrelated work preserved.

## What is established

The Nov 4–5 baseline remains **3,193.880989 Render MB**, comprising **1,573.21 MB HTTP Responses** and **1,620.68 MB Service-Initiated**. The latter is the largest unresolved billed category. **Its largest individual destination is still unverified.** Available historical measurements cannot honestly split it among Neon, R2, Google and Flight API.

Render's metrics API returns aggregate bandwidth. The authenticated dashboard adds traffic categories, but its bandwidth graph has no destination filters. The Neon connector again returned `MCP tool list_projects was not returned by tools/list`; existing authorized CLI discovery had not exposed NeoApps. A read-only SSH attempt using existing authorization failed with `Permission denied (publickey)`. No credentials, keys, roles or infrastructure were changed. There is no connected Cloudflare/RapidAPI usage tool.

Render bills outbound traffic, not inbound provider response bodies. Neon query results, Flight API JSON, Google Sheet responses and NOAA downloads must not be added directly to Render's Service-Initiated total. SQL requests/writes, archive uploads, API request bodies and network/TLS overhead remain possible contributors. [Render bandwidth accounting](https://render.com/docs/outbound-bandwidth), [metric categories and reporting delay](https://render.com/docs/service-metrics).

| Destination/process | Confirmed path and current evidence | Historical outbound volume |
|---|---|---|
| Neon PostgreSQL | External SQL reads/writes; no per-window project or socket-byte meter accessible | Unavailable. Prior monthly transfer/CU totals are not nightly figures and include database-to-app traffic. |
| Cloudflare R2 — FROST | Compressed evidence uploads plus HEAD/read-back verification; raw HRRR sources are content-addressed | Unavailable historically; new bounded counters distinguish upload body lengths from reads. |
| Cloudflare R2 — SPEAR/shared archive | Compressed PUTs and verified existing-object reads; existing content skips another PUT | Unavailable historically; separately labelled counters added. |
| Flight API | Existing `RapidApiFlightClient.fetch_fids()` sends a finite-timeout GET and uses bounded JSON reads (8 MiB ceiling) | Actual request/response byte totals unavailable. A response ceiling is not a measured response size or outbound volume. |
| Google | Existing durable poll lease/preflight coalesces clients; canonical readers use bounded reads; OAuth and authorized writes remain intact | No per-Sort provider meter. Browser execute POST counts do not equal upstream requests. |
| Other HTTPS | Brevo email and authorized integrations exist | No evidence identifies these as the dominant consumer. No speculative changes made. |

No redundant external transfer was independently measured strongly enough to justify deleting work, reducing polling or changing operational behavior before the Sort. Completed image/Dispatcher optimizations were not repeated. The **1.196 GB image encoding projection remains a projection**, not a change to the Service-Initiated category.

## Production observations before tonight

At approximately 20:18 CST, the existing read-only FROST status endpoint reported **enabled**, **outside configured Sort hours**, **8 research models**, and next capture **2026-11-06 03:00 UTC (21:00 CST)**. Historical acquisition queues showed zero pending sources/partitions. No acquisition/scoring request was triggered.

For the previous 03:00–11:00 UTC window, its eight indexed hourly captures showed five fully scored 8/8, one archived 0/8, one missed and one retained retry; together they recorded 186 resource deferrals. These are reliability/resource observations, **not 186 HTTP downloads or uploads**. No scheduling, model, memory-admission or archive-recovery rule was changed.

A fresh Render query covering 00:00–02:00 UTC returned an hourly point of 15.851737 MB at 01:00 UTC, representing the preceding 18:00–19:00 CST hour. More recent bandwidth points were not mature at inspection: hourly values can arrive about 60 minutes after the measured hour ends. They are not evidence of tonight's savings. Tonight had not started; there is no completed-night result.

## Minimal diagnostic change

Added `app/services/archive_transfer_diagnostics.py`, attached through the existing R2 factory. Existing FROST/SPEAR owned-client cleanup emits one aggregate stderr line per used client, labelled `frost_archive` or `spear_archive`. Nothing is emitted for unused/uninstrumented clients. No new timer, worker, poll, connection, database operation or configuration flag is introduced.

The counters record attempts, PUT/GET/HEAD/list counts, response/failure counts, first-request UTC, and **declared outbound/inbound HTTP body lengths**. HEAD's object Content-Length is correctly excluded from received-body totals. AWS decoded checksum-framing lengths and unknown lengths are explicitly counted. SDK retries remain unchanged and count as separate attempts. Streams are never read, rewound, cloned or retained. Logs contain no URLs, hostnames, credentials, object keys, user/mission identifiers or payloads.

Important limits: these counters are **not a wire meter or invoice reconciliation**. They omit request headers, TLS/TCP/DNS overhead, SQL and other providers. A declared incoming length does not prove a streaming body was fully consumed. Attempts without responses are visible through the counts. Use the first-request time and log timestamp to identify clients spanning a measurement boundary; do not arbitrarily allocate their whole volume to one hour. Diagnostic and logging failures cannot change request outcomes or suppress cleanup/primary errors.

**Measured validation:** 37 focused Python tests passed, plus 31 subtests, across diagnostics and the two affected archive modules, including six diagnostic cases. Installed botocore was exercised through a mocked HTTP transport: a five-byte PUT and HEAD produced two attempts, five declared outbound body bytes and zero HEAD download bytes; no real provider writes occurred. Existing idempotency, checksums/read-back, timeout/retry settings, stream cleanup and error sanitization passed. Unexpected attachment/emission failures were also verified not to prevent transport creation or client closure. Compilation and diff checks passed. Existing deprecation warnings remain visible.

A local 10,000-pair header-counter microbenchmark took 0.0396 seconds under tracemalloc (3.96 microseconds per request/response pair), with 1,085 peak traced bytes and no payload reads. This is a function-level overhead measurement, not an application load test or production performance claim. No browser suite or PostgreSQL tests were needed for these transport-only counters.

**Savings from this change: none claimed.** It supplies missing evidence to target the next safe optimization. It does not rewrite FROST/SPEAR transport, suppress legitimate retries or modify archives. The exact final LIVE SHA/deployment and read-only rollout verification are recorded in the completion receipt and local `instance/service-egress-20261105/release.json`.

## Reproducible comparison after tonight's Sort

1. **Use matching windows:** baseline `2026-11-05T03:00:00Z` to `11:00:00Z`; tonight `2026-11-06T03:00:00Z` to `11:00:00Z`. Both are 21:00–05:00 CST. At **06:15 CST / 12:15 UTC or later**, confirm that every hourly bandwidth point has arrived. Sum points labelled **04:00 through 11:00 UTC**, each representing the preceding hour; exclude the 03:00 point. An absent/unmatured point is unavailable, not zero.
2. Call Render `get_metrics` for resource `srv-d8buq2mgvqtc73e375j0`, workspace `tea-d79io9p4tr6s73cuk1bg`, `metricTypes=["bandwidth_usage","http_request_count"]`, matching start/end above, `resolution=3600`, `aggregateHttpRequestCountsBy="statusCode"`. Save raw timestamps/status series. Separately read the dashboard's eight hourly HTTP Responses and Service-Initiated tooltips. Tonight's exact range: [Render metrics](https://dashboard.render.com/web/srv-d8buq2mgvqtc73e375j0/metrics?t=1793934000000%2C1793962800000). Cross-check rounded category sums against the aggregate API; preserve provider units.
3. Record total requests and 429/500/502/503 separately. Check request bucket boundaries; for an exact access-log census use `start <= timestamp < end`, deduplicate log IDs and distinguish frontend metrics from app logs. Retain baseline **50,476 requests / 207 HTTP 429s** from the earlier aligned census; do not silently replace it with a differently bucketed metric sum. Deployments and diagnostic requests must be disclosed.
4. Collect **only image-related** app access logs for the same window, `limit=100`, forward cursor pagination, with gentle pacing. Count successful GET 200 and 304 by path; compare old large PNG URLs versus `/static/images/optimized/`. The logger's `200 0` does not mean an empty image. Multiply successful GET counts by verified served file bytes only as a reconstructed body estimate; keep it separate from billed HTTP bandwidth. Cache hits producing no request cannot be counted as identified users.
5. Collect app logs containing **`NeoApps archive_transfer`** for the same window. Sum declared outbound body bytes, PUT attempts, GET/HEAD/list counts and declared inbound bytes separately by component; retain unknown/decoded-length counts and boundary-spanning clients. Compare R2 upload totals with Service-Initiated as an incomplete body-level contribution, not an exact allocation. A stream with only reads/HEAD/list operations does not establish repeated uploads.
6. Obtain **NeoApps-specific authorized** Neon hourly compute/public transfer for both intervals if provider access becomes available. Do not substitute an unrelated project, a monthly counter or database-result bytes for Render egress. The still-missing decisive measurement is per-destination Render **TX/wire bytes** (especially PostgreSQL versus R2), plus provider request/body totals for Google/Flight API. No new permissions, extension or database configuration is authorized by this report.
7. Normalize with actual mission, assignment, completed-event and active-session/operator counts where an existing read-only source supports them. Baseline workload was 52–53 missions, 25–26 assignments and 23–25 events. Report missing session counts. Do not infer users from proxy IPs or claim deployment causation from incomparable traffic.

If only part of tonight is available, report completed hourly intervals and cumulative counters explicitly; do not extrapolate to eight hours. No automation or new recurring job was created. The shortest safe next action is to inspect tonight's archive counters, obtain the remaining destination/Neon measurements, and optimize only a demonstrated repeated transfer.

Local supporting evidence: `instance/service-egress-20261105/` (pre-Sort metric response, benchmark and release receipt) and `instance/service-egress-focused.log`. Production checks are read-only; no test fueling, Google writes, archive-health PUT, employee change, historical repair or cleanup is permitted or performed.
