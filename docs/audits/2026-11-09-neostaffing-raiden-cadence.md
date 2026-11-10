## Five-minute conditional Raiden publishing — 2026-11-09

Raiden publishing now uses the EXISTING bounded archive deadline timer, not a new worker.
During an active RFD Night sort, every next five-minute UTC boundary the publisher
calculates the canonical NeoStaffing Night/Ramp/Shift HERE count from the database.
It does NOT contact Google when the operation ID and count match the last
verified delivery, even if multiple supervisors saved attendance records in
the meantime. Dirty-revision tracking is retained to preserve concurrent edit
semantics, but every change preserves the existing next-check time and cannot
start an immediate write. When the count changes (or the operation changes),
the publisher reads Display!L23:P23, validates SHIFT/N23/RAMP guards, writes
ONLY Display!M23 if necessary, and independently reads back to verify.
The verified delivery time stays unchanged for internal no-op checks.

Failures remain durable and retry at 5, 10, 20, and 30-minute intervals
without external hammering; changes during backoff cannot shorten the delay.
Between Night sorts the last value is retained and no checks occur. At the
next active Night, a stale due check can immediately reset the tally and then
return to the five-minute cadence. No new database table/schema, Google sharing
or other Raiden cells are changed.

Validation: targeted test coverage for coalescing, zero-I/O unchanged totals,
net-zero attendance edits, failure backoff, operation change and cell guards.
