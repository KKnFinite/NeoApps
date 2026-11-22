# Phase 2 service traffic and database work

Issue #29. Starting production revision `c3062dcbb9ea0fb3fb788fff8bd835b3d2a2a1fb`, Render deployment `dep-dc197mbtqb8s73a9d2ug`. The release record and post-deploy observations are in the issue.

## What the providers measured

The November billing snapshot showed 21.54 GB service-initiated transfer and 8.45 GB HTTP response transfer. The available service metric has no peer, route or protocol dimension. Across 72 complete hourly buckets ending November 21 at 06:00 UTC, the service reported 5,037.88 MB of **aggregate** outbound bandwidth. The 72 hours and the billing period are different populations. The available Neon project counter was 91,283,542,689 bytes transferred out, 1,639,134 active seconds and 416,160 compute seconds. In particular, a query result sent by Neon goes *into* Render.

| Source and direction | Measurement | Limits |
|---|---:|---|
| Render to FROST archive | 22,846,760 outgoing body bytes, 1,228 requests | 199 PUT, 608 GET, 421 HEAD. Responses supplied 34,820,579 incoming body bytes. |
| Render to SPEAR archive | 466,933 outgoing body bytes, 1,416 requests | 472 PUT, 944 GET. Responses supplied 526,877 incoming body bytes. |
| Other service-initiated traffic before this release | No verified byte breakdown | Includes possible PostgreSQL requests and authorized providers. Subtracting the two archive totals from all Render traffic would mix accounting categories. |
| Browser responses, HTTP health checks, private networking | Distinct accounting boundaries | The health handler uses zero SQL on the existing fixture. No evidence assigns the unknown service traffic to health checks or to an internal private link. |

Archive counts come from the complete 622 transfer summary logs in the matching interval. These count request/response bodies, excluding protocol headers, TLS and retries below the instrumented transport. A GET or HEAD can send zero body bytes and still incur network traffic. Archive probe counters alone cannot establish missing receipts. See [Render outbound accounting](https://render.com/docs/outbound-bandwidth) and [service metrics](https://render.com/docs/service-metrics).

## Verified reductions

The same synthetic 52-mission Sort ran on baseline and candidate using fresh authenticated requests through a counting loopback PostgreSQL TCP relay. Three consecutive samples on each revision agreed byte-for-byte after warm-up. Sent means application to database. Received means database to application. Local PostgreSQL protocol bytes exclude production TLS and IP overhead.

| Route | Sent before → after, bytes | Received before → after, bytes | SQL statements before → after |
|---|---:|---:|---:|
| NeoSektor Driver Routing version | 6,486 → 3,667 | 5,267 → 2,554 | 4 → 3 |
| NeoScorpion Dispatch revision | 11,875 → 9,056 | 8,921 → 6,206 | 10 → 9 |
| NeoRaiden unchanged state (304) | 19,988 → 19,426 | 10,292 → 9,705 | 17 → 17 |
| RFD Gateway launcher | 22,801 → 22,239 | 19,919 → 19,331 | 24 → 24 |

The lightweight scope lookup selects the fields it uses and retrieves the current authorization-activation bit in the same database statement. Other paths load only the activation bit and its identity. The cache is confined to the request. Normal session lookup defers password, MFA-secret and administrative-note columns until a caller needs them. On Ermac View Outbound, Staffing Attendance, Rain Inbound and SubZero Operations, each request also sent 562 fewer serialized SQL bytes. Their statement counts remained 27, 24, 30 and 29 respectively.

The authorized MotherBrain live mission reader used to open a workbook to fetch metadata it discarded before reading the configured ranges. One values batch now retrieves those same ranges, including cancellation evidence. The local real-gspread transport measured **two GETs and 691 request bytes → one GET and 453 bytes**. The deterministic sample includes HTTP/1.1 request lines and headers with synthetic credentials omitted. Live response sizes and billable TLS traffic vary. Manual snapshot/metadata reads and scheduling are separate paths.

## Production attribution and recovery

The Linux PostgreSQL pool now observes cumulative TCP bytes acknowledged and received at existing connection returns. Only aggregate byte counts, check-ins and unmeasured returns leave the process, grouped by an allowlisted application name or background/shared work. There is no SQL execution, timer or new connection for measurement. The first interval includes connection setup. Delayed acknowledgments can move some bytes to the next borrower. Invalidated sockets and unflushed process tails can leave gaps. These are application socket measurements and cannot be equated directly to vendor billing.

The release changes no schema, permissions, hosting plan, autosuspend settings, integrations, physical Sort windows or worker deadlines. Roll back the release commit using the existing Render workflow and verify an authorized revision response, a denied request, normal mission ingestion and physical-window boundaries. No data reversal is required.

## Validation and inherited failures

The release selection passed 137 Python/PostgreSQL cases and 89 subtests. The Linux-only socket test was skipped on Windows and then passed on the local Ubuntu kernel using a real TCP connection. The cross-node group passed 126 tests and 357 subtests, with the same Windows skip. These groups overlap. Both Google variants used an isolated transport. The controls for authentication, scoped-authority revocation and operational permissions used synthetic accounts.

Eleven JavaScript cases now pass in the two previously failing suites. Three routing tests lacked a physical Sort window and a Dispatch test searched for an obsolete function signature. The original cadence, draft and retry assertions were retained. The PostgreSQL concurrent-reader test previously requested a revision outside the configured schedule. Supplying the synthetic active schedule restored the assertion that two independent readers see the subsequent committed change. The scoped access test again verifies one compound database read.

The wider Chromium/WebKit selection yielded nine passes and three failures. On the untouched production revision, the same three CSP-wrapped workflows failed on the same expectations: a fueler handoff reflected in a second tab, an Ermac lineup select reflecting a separate change, and a visible SubZero pretreatment indicator. They remain open. The prior release also records 59 Sektor and ten Ermac legacy assertion entries; this phase did not certify those entire suites. There were no production mutation tests.

## Follow-up measurements

Compare one full unscheduled day and two equivalent operational Sorts, normalized by active displays and traffic, before estimating savings from billing. The service did not record PostgreSQL peer bytes on the earlier revision. An equivalent retrospective production before/after by destination is unavailable. Keep the experimental one-minute worker discovery changes on hold. The production backup drill and privileged-account control evidence have a separate private disposition.
