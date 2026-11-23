# Phase 3 production measurements and release verification

This work started on production commit `0b811bb70218fdcdc813ec127ede9b22a476d160`, Render deployment `dep-dc19j85g1s2s73b3hbqg`. Issue #29 records the deployed revision and production checks. The PostgreSQL socket meter first went live at 07:29:54 UTC on November 22.

## Measured traffic

The RFD Night Sort windows below use the physical 21:00–05:00 Central schedule. Saturday is a complete unscheduled local calendar day. Render values sum settled hourly service buckets ending inside each interval. Neon active time is reconstructed from successful start and suspend completions on the production endpoint and branch, including its state at the opening boundary. A running endpoint can enter a window with zero new starts.

| Pre-release window, UTC | Render requests | Render outbound MB, all classes | Neon active hours | Neon starts | FROST archive outgoing / incoming body bytes | SPEAR archive outgoing / incoming body bytes |
|---|---:|---:|---:|---:|---:|---:|
| Nov 19, 03:00–11:00 | 49,112 | 1,976.50 | 8.00 | 0 | 7,018,148 / 14,119,362 | 157,899 / 178,600 |
| Nov 20, 03:00–11:00 | 43,174 | 1,880.80 | 8.00 | 0 | 7,825,143 / 12,492,793 | 156,890 / 177,210 |
| Nov 21 06:00–Nov 22 06:00 | 1,300 | 40.24 | 21.21 | 73 | No transfer summaries | No transfer summaries |

The two Sorts respectively had 433 and 473 FROST archive requests, and 489 and 480 SPEAR archive requests. These are exhaustively paginated transport summaries emitted within the windows. SPEAR performs an existence read, conditional immutable creation and independent content verification. An expected missing object on the first GET can increment the raw error counter. These counters alone do not establish a failed archive delivery.

For two equivalent five-minute intervals at 02:00–02:05 Central within those Sorts, the application access logs recorded:

| HTTP response bodies from Render | Nov 19 | Nov 20 |
|---|---:|---:|
| All routes | 13,534,474 bytes / 1,009 requests | 8,400,977 bytes / 736 requests |
| NeoScorpion | 8,149,027 / 160 | 5,646,997 / 163 |
| NeoSubZero | 3,387,711 / 27 | 2,636,413 / 26 |
| NeoErmac | 1,523,956 / 39 | 3,485 / 3 |
| NeoSektor | 24,170 / 94 | 52,572 / 208 |

Saturday's application access logs accounted for 2,517,933 response-body bytes over 1,330 requests, including health checks. Edge request counts and bandwidth have different boundaries from Gunicorn response logs. Application bytes can precede compression. They cannot be subtracted directly from billed egress.

For the first quiet Sunday sample after the database meter release (07:30–08:20 UTC), six emitted PostgreSQL observations recorded **478,201 bytes acknowledged from Render to Neon, 464,274 bytes received and 75 connection returns**, all in background work. The same interval contained zero archive summaries, 12 application HTTP responses totaling 23,032 body bytes, and 13 Render edge requests. The corresponding Sunday interval seven days earlier recorded 203 application requests and 94,719 response-body bytes. Different activity, intervening releases and the absence of a historical database meter preclude attributing the change to a particular fix.

The November billing snapshot separated 21.54 GB service-initiated traffic from 8.45 GB HTTP response traffic. The service metrics for the historical windows do not contain destination labels. Data returned *from* Neon is inbound to Render. The new operation-level instrumentation measures declared Sheets HTTP body lengths on the existing MotherBrain mission reader and Staffing Hours Log publisher, recording counts and unknown lengths without opening another connection, reading a response twice or starting a timer. It excludes token exchange, failed connections, transport overhead and retries below the response hook. There is no retrospective Sheets byte series. The remaining service-initiated traffic cannot be apportioned reliably or used to justify a change to worker deadlines. See [Render's bandwidth accounting](https://render.com/docs/outbound-bandwidth).

## Database restoration

The earlier local drill removed its disposable destination. This run restored a new synthetic PostgreSQL custom-format dump in a single transaction with exit-on-error and retained both local restore attempts. Verification against the retained source manifest used a repeatable-read, read-only transaction. Across 183 tables and 258 records, the canonical SHA-256 of all row values and every per-table count matched. There were 52 missions, two attendance entries, four ballmat wave counts, two wave states, one pull and one deice event.

All 918 constraint definitions matched after normalizing PostgreSQL's equivalent array-literal casts. All 435 foreign keys had zero orphans, all 178 CHECK expressions had zero violating rows, all seven application triggers matched, and no triggers were disabled. The dump used a disposable schema and excluded ownership and ACLs. Production snapshot replay, credentials, privileges, production-wide RTO/RPO and application failover are outside this result. A provider snapshot was listed, but no existing disposable Neon branch was present for verification.

## Release scope and follow-up

The release aligns existing sessions and locked administrative writes with the current sign-in policy, and restores the recorded PRETREATED marker to the compact SubZero outbound row. Synthetic tests check that the adjacent aircraft does not inherit the indicator. Fueler handoff and Ermac lineup fixtures now exercise actual physical Sort hours. Scoped administration tests use the native fixture, real navigation and unchanged permission assertions. Private commissioning details are kept out of this report.

For comparison after release, collect both physical Sorts beginning November 23 and 24, and a complete unscheduled day. Retain the same UTC boundaries, branch and endpoint, deployments, number of missions, active displays, route mix and archive volume. Use organization billing for period CU-hours, transferred bytes and invoices. The branch counters update in batches and cannot be apportioned to these periods by subtracting two nearby observations. No dollar savings or full-month destination split has been verified.

### Focused release checks

| Validation group | Result on the candidate |
|---|---|
| Google bounded transport, MotherBrain reader, Staffing Hours Log including disposable PostgreSQL, and Rain Sort windows | 73 passed, 42 subtests passed |
| Account and request security including PostgreSQL | 40 passed, 6 subtests passed; the final account and concurrency rerun with the new lock-wait case passed 24 tests, 4 subtests |
| Fuel Dispatch and Staffing saves with expired CSRF tokens and enforced CSP | 1 passed, including Chromium and WebKit fueler handoffs and the Staffing editor |
| Ermac CSP and scoped administration | 2 passed, 4 subtests passed |
| SubZero Outbound lifecycle plus aircraft-specific pretreatment indication in Dispatch List and Ramp Columns | 2 passed, 4 subtests passed on Chromium and WebKit |
| Physical-window browser tests | 2 passed, 24 subtests passed |
| JavaScript live revisions, CSRF renewal and draft restoration | 44 passed |

The groups overlap. The broader legacy SubZero CSP scenario passed its original pretreatment assertion after the repair, then stopped at a hidden desktop accordion summary. The compact desktop list exposes an inline chevron and autosaves pass fields. This older end-to-end scenario still requires migration; its later steps are unverified by that scenario. The separate current Outbound lifecycle exercises actual actions in both engines. No operational rule was relaxed for these tests.

The changes require no migration. Roll back the release commit through the existing Render service, verify health and static-asset hashes, exercise the protected routes and check worker receipts. The release does not reconfigure Google integrations, credentials, grants, worker discovery or database suspension.

## DUCC and Fuel Dispatch measurements before the November 23–24 Sorts

Compared the same signed-in local requests at `4bfb8ff3` and with the DUCC crew-grid change. The DUCC fixture had two departures, two eligible employees and 28 crew positions. CSRF protection was enabled. Both screens used a five-second revision interval in these fixtures. A revision change or an authorized save can fetch a board. An idle five-second interval does not imply a full-board transfer.

| Request | Response bytes, before → after | Gzip bytes, before → after | SQL reads, before → after |
|---|---:|---:|---:|
| DUCC first crew fragment | 259,266 → 239,740 | 11,627 → 11,547 | 48 → 42 |
| DUCC subsequent weather change | 259,266 → 22,868 | 11,622 → 4,165 | 48 → 42 |
| DUCC subsequent change to one ramp’s crew | 259,815 → 54,329 | 11,859 → 6,488 | 50 → 44 |
| DUCC revision check | 282 → 282 | approximately 200 | 18 → 12 |

Gzip figures are local level-six measurements and can vary slightly with session tokens. A warm request sends up to 454 bytes of grid hashes plus the header name and framing. All measured GETs issued zero database writes. The server still renders and authorizes the current board. Only byte-identical crew grids from the same session and operation are eligible for reuse. A current unit-move version and form token accompany every negotiated fragment.

Fuel Dispatch on the one-mission fixture returned 140 bytes and used 10 reads for the revision, 464 bytes and 51 reads for an unchanged critical delta, 43,915 bytes and 51 reads for a changed critical delta, and 57,609 bytes and 62 reads for the full recovery panel. These measurements matched on both revisions. The unchanged critical projection is a remaining database cost to compare with its production request frequency.

Keep the November 18–19 production figures above as the pre-release baseline. The comparison windows are November 24 and 25, 03:00–11:00 UTC, corresponding to the two physical Night Sorts beginning November 23 and 24 Central. Match Render deployment finish times to those windows, record any revision changes within each window, and normalize DUCC fragment bytes per response and revision requests per active screen-hour alongside mission volume. Use the existing provider and application telemetry. Keep HTTP bytes, database reads, CU-hours and invoiced charges separate. The release receipt in issue #29 identifies the deployed revision.
