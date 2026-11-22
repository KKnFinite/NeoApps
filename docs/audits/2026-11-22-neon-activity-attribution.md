# Who used the production Neon database?

Evidence for NeoApps issue #29, November 22, 2026. Production at the start of the investigation was `439c896fa48d9293f307744316cfa3c3054ee3ec` on Render Starter, deployment `dep-dc1alnm417fc73bbijcg`. Times are US Central standard time unless marked UTC.

## Off-hours activity

**Scheduled server work explains the recurring wake-ups in the quietest observed interval.** On Saturday from 03:00 to 08:00 there were no application HTTP requests, deployments or Gunicorn worker boots. Neon started 18 times. After each start the existing archive scheduler logged a completion checkpoint, 0.876–2.111 seconds later (median 1.712 seconds). The endpoint was active for 87.5% of these five hours.

| Observation window | Render application HTTP requests | Render deployments overlapping window | Neon starts | Endpoint active time | Starts near archive scheduler checkpoint |
|---|---:|---:|---:|---:|---:|
| Saturday Nov 21, whole unscheduled day | 1,331 | 14 | 73 | 21h 12m 45s | 73 of 73 |
| Saturday, 03:00–08:00 (subset) | 0 | 0 | 18 | 4h 22m 24s | 18 of 18 |
| Sunday Nov 22, 00:00–03:00 | 48 | 3 | 10 | 2h 32m 34s | 10 of 10 |
| Sunday, 01:45–02:42, during documented local testing (subset) | 0 | 0 | 4 | 45m 51s | 4 of 4 |

The 24-hour comparison used a checkpoint from five seconds before to two minutes after a start. For 71 of the 73 Saturday starts the checkpoint was within three seconds. Only five starts were within two minutes of any app HTTP request. None happened during a Render deployment or within two minutes of a worker boot. The 14 deployment windows overlapped just 20m 24s of active compute. The timestamp association does not identify which database client opened the first connection.

Across Saturday, approximately **16h 45m of the 21h 13m active (79%)** lay outside both HTTP activity and deployment windows, even allowing the idle tail. This comparison excludes the first five minutes of the day, the interval from 30 seconds before through five minutes after every application access-log entry, and every deployment through five minutes after it ended. It measures elapsed overlap. Direct external database clients are invisible in the HTTP logs.

Both the archive planner and the employee-recovery worker run in the web service and consult PostgreSQL at aligned ten-minute safety boundaries. The planner also honors durable deadlines between those boundaries. In the five quiet hours the server logged 39 archive checkpoints, including ones between the ten-minute marks, and 30 departure-reconciliation diagnostics. These diagnostics are rate-limited. A departure reconciliation and a model prediction are different operations. Neon's five-minute inactivity period keeps compute active after the last database access. For 3h 13m of that five-hour interval, the endpoint was both active and within five minutes after an observed archive checkpoint. The remaining active time cannot be assigned to a specific job from those checkpoints.

## Who generated activity?

| Source | Production observation | Limit on attribution |
|---|---|---|
| Local development and Codex tests | Documented test runs targeted in-memory SQLite and disposable loopback PostgreSQL. During a 57-minute interval with local testing, the four production starts all coincided with the server scheduler. The tested development shell had no production database URL configured. | Historical production connection logs were unavailable through the Neon log interface. Other workstations, one-off scripts and past direct connections remain unattributed. |
| Deployment and release verification | The Render pre-deploy command runs the database bootstrap. One identifiable pre-deploy meter record on Sunday at 08:42:56 UTC accounted for 82,887 bytes sent and 34,078 received. Three ten-request production smoke sequences match the release checks. Locally, the corresponding health, static and unauthenticated routes incurred zero SQL. | A short-lived deployment can exit before the passive meter flushes its final batch. The cited record is a lower bound for that deployment. |
| People performing operational work | At 02:00–02:05 during the Nov 19 and Nov 20 Night Sorts, logs show 30 and 29 POSTs to fueling and Ermac operational actions. These include interface saves and acknowledgments. | HTTP requests measure activity. They cannot establish a unique employee count, physical presence, SQL per interaction or employee-attributable CU-hours. |
| Computers, phones and open tabs | In the same two five-minute intervals, 1,009 requests included 321 desktop, 684 mobile/tablet and four automation requests. The next sample had 736 requests, including 436 desktop, 294 mobile/tablet and six without a device category. Of these, 255 and 349 hit revision, version, state, live-panel or heartbeat URL suffixes. | These categories overlap the employee actions above. An attended screen and an unattended one can send identical requests. The passive production DB meter was installed after both Sorts. |
| Server workers and integrations | Between meter activation at 07:29:54 UTC and 09:00 UTC Sunday, ten web-service process aggregates recorded 115 non-request pool returns, 731,585 bytes sent and 798,381 received. One additional aggregate came from pre-deploy. No request-context pool returns were reported. | These are socket-byte deltas reported on pool return. Individual shared jobs, external integrations and any direct database clients have no separate historical query or active-time totals. |

One deliberate Codex read-only production statistics inspection took place after these windows, around 09:05 UTC. The endpoint was already active. It was an investigation query and could extend the idle tail. No production operational records were changed for testing.

## Open screens and production checks

The complete Saturday access log contained zero requests ending in the five refresh-route suffixes above. Sunday's eight such requests were anonymous protected-route checks returning HTTP 302. An all-day URL count alone cannot identify every client timer. The signed-in browser regression exercised 11 operational screens on desktop Chromium and phone WebKit, with idle time, visibility changes and page-show events, plus the restricted workspace across physical Sort opening and closing. Both tests passed, with 24 subtests. No off-hours automatic operational requests were observed in these checks.

The original two eight-hour Night Sorts had 49,112 and 43,174 Render edge requests. Neon was active for all eight hours of each Sort with **zero** starts inside either interval because compute was already running. A database connection, an SQL statement, a wake-up, elapsed active time and a billed CU-hour are separate measures. Production did not expose historical per-query counts or a per-client CU-hour series. The active-time calculation uses successful production endpoint start and suspend completions, including the state immediately before each window. Render edge totals and application access logs cover different request populations.

## Verified reduction to deployment activity

On each release the PostgreSQL compatibility check inspected every visible table's columns separately. It now requests the same catalog metadata in a batch. The same column sets drive the existing compatibility checks, repairs and backfills.

| Production bootstrap command on isolated loopback PostgreSQL | SQL statements before → after | Pool checkouts before → after | New physical connections before → after |
|---|---:|---:|---:|
| Fresh disposable database | 1,663 → 1,483 | 207 → 25 | 2 → 2 |
| Already initialized database, first repeat | 800 → 620 | 207 → 25 | 2 → 2 |
| Already initialized database, second repeat | 800 → 620 | 207 → 25 | 2 → 2 |

The reduction per measured deployment is **180 SQL statements and 182 pool checkouts**. On the unchanged schema, read statements fell from 708 to 528 and all 11 DML statement attempts were retained. Reverting this change restores the individual catalog reads. It does not alter the schema, worker deadlines, browser refresh gates or production database settings.

Validation passed 90 bootstrap, database compatibility, archive, deadline, account and mode-transition tests, plus 18 subtests, using local SQLite and disposable PostgreSQL. The legacy-schema case upgraded twice while retaining mission IDs, pull history and lineup geometry. The separate physical-hours browser suite passed two tests plus 24 subtests. Render deployment verification is recorded on issue #29.

For scale, an empty employee-recovery check issued one SELECT in the test database, and the shared deadline planner issued 16 SELECTs in one checkout for the synthetic RFD fixture. Production workload and query counts vary with the data. The deployment improvement does not address unattended-day wake-ups. Changing the durable discovery interval would require proving that a surviving worker can discover deadlines committed by another writer after a notification is lost. The held worker candidate was excluded. No billed savings or reduction in production active time has yet been demonstrated.
