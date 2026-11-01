# Background deadlines and Neon idle windows

The existing web-worker recovery loop and archive heartbeat use UTC deadlines.
Neither starts in Gunicorn's parent, creates a new service, nor holds a checked-out
database connection while waiting. Existing SQLAlchemy pooling is unchanged.

* Recovery reads the earliest DELETED/RESTORED expiration. Empty/unexpired tables
  are not purged or committed. The next check is the earlier of that exact expiry
  and a 600-second cross-process safety check. Expired batches retain the existing
  25-item limit, SKIP LOCKED/identity locks, encrypted payload handling and retries.
* Archive scheduling reads compact receipt minima and bounded discovery cursors.
  Pending retries keep their existing timestamps/leases. Discovery batches finding
  changed/new evidence continue promptly; unchanged historical batches stay at the
  600-second safety cadence. Unconsumed durable audit hints are prioritized. A
  restart can lose only a fast-scan hint, never its durable discovery progress.
  Delivery retries can run without reconstructing SubZero history.
* FROST wakes at the next UTC hour or an earlier staged/current-slot retry. Due
  hourly acquisition runs before other archive work in that gateway. Shared-lease
  contention retains a short check for an unstarted current slot. Existing
  acquisition, timestamp, gap, memory, scoring and R2 verification contracts remain.
* An outer committed evidence mutation provides a coalesced in-process wake hint,
  normally within 1–30 seconds. Reads and rolled-back transactions provide none.
  Notifications perform no database/network I/O and cannot fail an already
  committed operation. A crash or missed/cross-process hint recovers at startup or
  the next safety scan (at most 600 seconds during an otherwise idle wait).

Existing timers still perform cheap, connection-free wall-clock checks at most
60 seconds apart. Thus a timer wake is not evidence of a database wake. Backward
clock changes recompute deadlines; all scheduling uses UTC rather than local DST.
There is one current archive timer, with generation fencing for canceled callbacks.
Receipt leases and attempt fencing remain the cross-process delivery authority.
If allocating a replacement timer fails, the existing recovery clock repairs that
specific failure without querying the database or creating another worker.
Both systems align safety scans to the same UTC ten-minute boundaries. Local
notifications/work do not drift their idle scans into alternating five-minute
database wakeups. Earlier real retry/expiration/capture deadlines still win.

An idle interval exceeding 300 seconds is possible only when there is no earlier
legitimate deadline. Active decisions, archive retries, recovery expiration,
resource deferrals and unfinished discovery may correctly keep Neon active.
This change does not modify Neon autosuspend, Render settings, or claim CU savings.

Focused deterministic SQL tests: `tests/test_background_deadlines.py` and
`tests/test_background_deadlines_postgres.py`. The latter refuses non-loopback or
non-test databases and uses disposable schemas. Recovery and immutable archive
failure/concurrency suites remain independently applicable. Production logs show
the next recovery and archive UTC deadlines without employee/source payloads.
