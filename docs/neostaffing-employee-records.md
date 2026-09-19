# Employee Records

Manual Talk With, Verbal and Written Warning documentation. Nothing in attendance,
accountability thresholds, or discipline delivery creates a record automatically.
Creating/finalizing a document never delivers an obligation or advances discipline.

## Authority and lifecycle

Central active NeoStaffing management/leadership scope controls every operation,
including image reads. Work assignments and leadership units are current canonical
data; a managerial target unit requires authority across its descendant work areas.
Creating a record grants no lasting access. After a transfer, current scoped management
can see all retained records; previous managers cannot. Historical org snapshots do
not authorize reads. Grandmaster's historical fallback is **read-only** for inactive
or currently unassigned people; it is not an active-employee write bypass.

Operation ON overrides Department; otherwise Department controls its Work Areas.
Settings default OFF. Configuration uses Staffing Master plus current full-unit
leadership scope, matching the existing Tracker configuration pattern.
OFF prevents creation only: authorized people may still review/edit existing drafts,
finalize them, read finalized originals, and append dated context.

Draft versions reject stale edits/reviews. Employee parent and document row locks
serialize competing writers with canonical assignment changes. Fresh/locked user,
app access, management identity, leadership and hierarchy dependencies guard writes.
Finalization requires explicit in-person review and either drawn initials/signature
or explicit RTS (Refuse to Sign). The acknowledgment is exactly:

> This information was reviewed with me.

Final originals cannot be edited/deleted through normal application paths. PostgreSQL
and SQLite triggers also reject UPDATE/DELETE of finalized originals and all event
history. Addenda are append-only events with stable UUID, actor, UTC timestamp and
sequence; concurrent/replayed addenda reject stale sequence tokens. No delete route
or automatic Employee Records retention purge exists.

Creation freezes organization IDs/names. An optional existing discipline resolution
must belong to the same employee. Its stable ID and compact source snapshot are
retained without a cascading FK: normal nine-month discipline cleanup cannot erase
the personnel document or break its linkage. No discipline tables are changed.

## Private signature storage — production setup required

Default: `EMPLOYEE_RECORD_SIGNATURE_STORAGE = None`. Signature finalization fails
closed and leaves the draft unchanged. RTS remains available only for actual refusal,
not as a suggested workaround for a storage outage. No production bucket was configured
or contacted by this implementation.

The deployment's Flask configuration may inject a durable **private** adapter at
`EMPLOYEE_RECORD_SIGNATURE_STORAGE`. It must implement:

* `put(key, bytes, content_type='image/png', checksum=sha256)` — durable, private,
  put-if-absent, immutable object; raise on failure. Return only after durable success.
* `get(key, max_bytes=150000)` — return bounded bytes; never log object contents.

Use dedicated personnel-storage credentials/bucket/namespace, encryption in transit
and at rest, restrictive provider IAM, and no public URLs. Do not reuse SPEAR's
learning-data bucket. Keep provider secrets in deployment secrets, not source.
The adapter must retain objects for the lifetime of these records and preserve old
keys if its infrastructure changes. This is an interface, **not a configured provider**.
Review unreferenced objects from interrupted DB commits manually; do not delete any
referenced signature. No new poller, cache, cleanup job, or background worker is added.

The server accepts bounded PNG only, validates dimensions and image decoding,
rejects blank pads, and re-encodes without metadata. Raw bytes are never SQL values,
session values, logs, repository files, or local-storage values. DB rows contain only
object key, SHA-256 and size. Image retrieval reauthorizes current scope, checks the
checksum and responds `no-store, private`; no public/presigned URL is exposed.

## Schema / validation

Three additive tables: `staffing_employee_record_settings`, `staffing_employee_records`,
`staffing_employee_record_events`. The approved explicit schema-sync missing-table
bootstrap creates them in dependency order and installs immutability triggers as part
of table creation. Existing populated tables are untouched. Repeated bootstrap is safe.
Render's existing pre-deploy bootstrap must finish before readers start. No manual deploy.

Directory and history are paginated at 25; scope queries are set-based with no per-person
lookup. Record screens have no polling. Signature pad requires JavaScript; without JS,
RTS remains a standard protected form and unsigned signature finalization fails closed.

Tests: `test_neostaffing_employee_records.py` and opt-in
`test_neostaffing_employee_records_postgres.py`; PostgreSQL accepts only disposable
loopback `neostaffing_test_*` databases, using isolated schemas. Browser proof uses
fixture employees only, not production personnel data.
