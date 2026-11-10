# NeoStaffing Google publishers — active RFD Night sort window only

Change scope: both existing NeoStaffing external Google integrations:
- Production Hours Log / Shift Staffing roster
- RFD-N-sim: Raiden / Display!M23 HERE count

A *scheduled* Night Sort Date Operation can appear in the current-operations
selector before its configured start (or after its end), so existence of an
operation was not proof that a Google call was allowed. Production Hours Log
did not gate its deadline or drain at all, and could contact Google off-sort.

Both publishers now use a shared RFD Night gate based on the configured
Sort Timeline `sort_window_start_local` and `sort_window_end_local`, the
gateway's local time, and `operation_is_active_at`. Overnight end after
midnight is handled by the existing gateway_matrix rules; start is inclusive
and end is exclusive. When no configured boundaries or active Night operation
exist, the gate fails closed (never the old 00:00–23:59 fallback).
Both `next_deadline` and `drain` return without Google API reads/writes when
outside. A second guard before opening the Google client handles a window
closing during snapshot preparation. Pending database delivery receipts and
retries are preserved, not discarded. Employee/attendance edits outside the
sort can still persist locally as intents for the next eligible sort.
Internal lightweight background scheduler checks continue at existing cadence;
there is no Google polling outside the window.

Raiden retains its five-minute, change-only check during the active window.
Hours Log retains its existing revision/lease/verification semantics.
No sheet contents, row formulas, external data or UI settings are changed by
this code change. Synthetic regression tests cover before/at/after the sort,
overnight rollover, no configured window, no provider access outside window,
and continuity of queued receipts.
