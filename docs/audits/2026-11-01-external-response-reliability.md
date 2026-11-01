# Task 8: external-response reliability

Baseline: `e5a37498fe541da8995325de1458c9c0ba44caec`.

## Transport bounds

| Request | Wire and decoded body maximum | Existing timeout |
| --- | ---: | ---: |
| AeroDataBox airport FIDS | 8 MiB | 20 seconds |
| KRFD AviationWeather METAR (two hours) | 256 KiB | 8 seconds |
| NWS KRFD points metadata | 256 KiB | 8 seconds |
| NWS hourly forecast, full provider horizon | 1 MiB | 8 seconds |
| NWS grid forecast, full provider horizon | 4 MiB | 8 seconds |
| Existing FROST recent-METAR caller | 512 KiB, unchanged | 8 seconds |
| Brevo transactional-email receipt | 64 KiB | 10 seconds |

FIDS needs room for a busy-airport operational window and nested movement
metadata. Weather bounds allow substantially more than a single-station report,
point metadata, or the normal seven-day NWS time series. No date window, flight
filter or forecast horizon is shortened. Limits are fixed in provider adapters;
callers cannot accidentally omit a weather limit.

The shared urllib reader consumes at most maximum + 1 wire bytes in bounded
chunks, including partial reads. An oversized declared length is rejected before
reading. Gzip and zlib-wrapped deflate have separate bounded output, EOF/trailer
validation, and no unbounded flush. Unsupported encodings, invalid JSON, nonfinite
constants and invalid provider containers are controlled failures. Nothing is
silently truncated. Callers own/close successful responses and HTTP-error bodies.

Brevo was the additional closely related unbounded direct-urllib JSON reader.
An invalid/lost email receipt is unsuccessful/unverified under the existing email
contract; this code never replays the POST. Fixed diagnostics omit provider bodies
and credential-bearing exception text. Google SDK transports and HIBP's advisory
text API are separate adapters and are not changed by this release. Existing
bounded FROST autonomous/historical transports remain unchanged.

## Weather failure cache

METAR, NWS points and NWS forecast each have process-local failure state. Failed
attempts wait 60, 120, 240, 480, then 900 seconds, capped at 900 thereafter.
Success clears that source's failure state. No database coordination or polling
is added. In-flight keys are single-flight; other callers immediately receive
bounded-age last-good data or unavailable. Provider I/O does not hold the shared
cache lock, so independent sources can progress. Forecast still requires NWS
point metadata; failure never substitutes future weather or another station.

Successful TTLs remain 5 minutes / 30 minutes / one day for METAR / forecast /
points. Stale limits remain 6 hours / 24 hours / 7 days from the original successful
fetch. Failed attempts never move that timestamp. Public revisions exclude retry
counters and errors, but include fresh-to-stale transitions so live boards can
display their actual freshness. Forecast issue and observation times are intact.

## Storage ownership

Task 7 already owns and closes independent SPEAR/SubZero SDK clients and object
bodies inside a context-local operation. Those formats, conditional writes,
read-back validation, crash leases, delivery states and `verified_at` are unchanged.

FROST's private client factory now requires its existing operation scope rather
than allowing an unowned client to escape. Nested calls borrow that scope and
never close its active client. Credential/configuration switches close the previous
client exactly once, including when replacement construction fails. A cleanup
exception is logged safely without masking the original provider failure or a
completed verified read-back. No cache, thread, worker, SDK dependency or schema
is added. No production archive backfill or test writes are performed.

## Validation caveats

The affected 212-test Python regression run passed 210 tests. Both remaining
flight-page failures reproduce with the starting commit loaded in memory:
`test_flight_api_review_departure_rows_show_schedule_api_local_times_and_diff`
(date-dependent display expectation) and
`test_stale_auto_poll_client_is_rejected_before_database_access` (one SELECT).
The existing JavaScript `flight_api_auto_poll.test.js` cannot load its script
fixture because it expects bare `<script>` markup predating trusted CSP nonces;
neither that test nor its template is changed here. The unrelated known FROST
asset-count assertion is excluded from the nine focused storage regressions.

Local fake-provider tests establish read bounds and retry behavior, not a measured
production memory saving. Actual outages and oversized responses are not induced
in production. No operational records, archived evidence, permissions, model
definitions, hosting settings or unrelated working-tree files are changed.
