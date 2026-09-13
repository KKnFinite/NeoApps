# NeoStaffing Timecards

## Ownership and time basis

NeoStaffing owns timecards. Sektor and Ermac select their existing employee
roster and expose `ATTENDANCE | TIMES`; neither node gains attendance authority.
Active management identity and centralized leadership scope authorize time
edits. Configured FT Combo management on either side can edit both assigned
Sort slices. Master access alone permits complete reporting/archiving, not
out-of-scope time edits.

A slice is unique by employee, Sort-start calendar date, and Staffing Sort.
Its real `SortDateOperation` is retained. Overnight work belongs to the date
the Sort started. Sunday–Saturday reports aggregate independent Sort slices;
they do not merge FT Combo attendance. Existing chronological Sort pairing is
used for partner presentation and missing-half exceptions.

Segments retain exact UTC timestamps, including fractional seconds, and the
source gateway timezone. Full offset timestamps are accepted; `HH:MM` shortcuts
use the slice date and roll an earlier end clock to the next day. Ambiguous DST
clocks require an explicit offset. Incomplete segments remain visible exceptions.
Exact durations are summed before rounding decimal hours to two places. Gaps
are unpaid. Only `Here` counts; other statuses display their actual value and
retain previous segments and audit events without counting those hours.

Historical employee/org labels are snapshotted from the attendance source area.
Later transfers/renames do not silently rewrite a downloaded labor report.
Authorization still follows current leadership and current assignments.

## Transactions and schema

Four additive tables: `staffing_timecard_weeks`, `staffing_timecard_slices`,
`staffing_timecard_segments`, `staffing_timecard_edits`. The existing Render
pre-deploy schema/bootstrap path creates missing tables and idempotently
projects retained, operation-bound attendance. It does not fabricate expired
attendance or change existing attendance, assignment, or discipline schemas.

Validated attendance writes synchronize the slice status within their existing
transaction. Time writes lock ordered employee parents, then ordered week
receipts, refresh their state, and check expected slice versions. Bulk changes
are atomic. Stale writes are rejected, never automatically replayed. Every
report-affecting change increments the whole-week receipt version and cancels
its pending purge. No-op saves leave versions and deadlines untouched.

The attendance writer reuses its post-lock child projection and existing
hierarchy; its established 11-SELECT test remains unchanged. Timecard GETs have
no writes or polling. Reports aggregate/page in SQL; exceptions page bounded
candidates (Combo candidates use canonical chronological checks). Adjacent-day
overlaps are checked even when the other slice is outside the displayed page.

## Downloads and retention

`DOWNLOAD SCOPED REPORT` is leadership-scoped and **never schedules deletion**.
Only NeoStaffing Master+/appropriate admin can request `COMPLETE WEEK ARCHIVE`.
That package ignores area/employee filters and includes all retained slices for
the completed Sunday–Saturday week, including inactive/out-of-scope employees.

The ZIP contains:

- `timecards.xlsx`: Summary, All Employee Hours, Employee Weekly, Sort Labor,
  Exceptions; filterable Excel tables, frozen headers, source organization fields.
- `timecards.csv`: source identities, actual status, exact UTC segments,
  timezone, version and actor metadata.
- `edit-history.csv`: retained edit identities, actor/time and snapshots.
- `manifest.json`: week, UTC generation time, counts, data/format version,
  prior purge count and component SHA-256 hashes.

Files are generated in memory, never stored in PostgreSQL. The optional PDF is
not generated; XLSX sheets have landscape print setup for static review.

Generating a package does not arm purge. After the browser receives the entire
ZIP, it starts a normal file download and acknowledges a signed actor/week/
version/hash token. The server rechecks the week under lock before recording a
three-day deadline. This confirms browser receipt, not an OS-level guarantee
that a user kept the file. Interrupted transfers do not acknowledge. Duplicate
acknowledgements do not extend the deadline. Any intervening edit rejects the
acknowledgement or invalidates an already acknowledged archive; complete
re-download is required. Scoped exports cannot acknowledge complete archives.

The independent one-calendar-month limit always applies. Existing user-driven
attendance maintenance physically deletes at most 250 slices plus their
segments/audit rows per invocation, oldest first, and examines at most eight
due archive weeks. No new worker, polling or scheduled service is added. Thus
physical cleanup runs on the next existing maintenance invocation, not a new
wall-clock job. Receipt-only cleanup is committed even for empty weeks. A
purged week cannot be recreated by subsequent attendance projection. Compact
archive/purge receipts remain; attendance/accountability facts are not deleted
by timecard cleanup.

No manual Render deploy or credential/environment configuration is needed.
Keep the existing pre-deploy bootstrap enabled. Download and retain the ZIP
outside Neo before relying on it as the long-term record.
