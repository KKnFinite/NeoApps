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
Finalization is supervisor confirmation: **Delivered / Discipline Given**. The
supervisor checks "I reviewed and delivered this action to the employee" and submits
the current draft version. The existing `finalized_by` and `finalized_at` fields
store the delivering actor and UTC time; `acknowledgment='delivered'` distinguishes
this from historical employee acknowledgments. An append-only event records delivery.
No employee account, signature, initials, RTS, JavaScript or object storage is needed.
Old signature/RTS forms are rejected and must be reloaded; they cannot silently
become supervisor delivery. Delivery documents the action but does not independently
resolve or advance a linked Discipline Tracker obligation.

Final originals cannot be edited/deleted through normal application paths. PostgreSQL
and SQLite triggers also reject UPDATE/DELETE of finalized originals and all event
history. Addenda are append-only events with stable UUID, actor, UTC timestamp and
sequence; concurrent/replayed addenda reject stale sequence tokens. No delete route
or automatic Employee Records retention purge exists.

Creation freezes organization IDs/names. An optional existing discipline resolution
must belong to the same employee. Its stable ID and compact source snapshot are
retained without a cascading FK: normal nine-month discipline cleanup cannot erase
the personnel document or break its linkage. No discipline tables are changed.

## Dormant historical signature support — no setup needed for delivery

Default: `EMPLOYEE_RECORD_SIGNATURE_STORAGE = None`. The current UI and finalization
path do not accept or store signatures or RTS. Historical signature/RTS metadata,
external references and immutable events remain unchanged, not relabeled as delivery.
The secured historical-image read endpoint and storage adapter are dormant compatibility
support. No production bucket is required, configured or contacted by this workflow.

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

The dormant storage helper validates bounded PNG and strips metadata. No active
record finalization accepts an upload. Raw bytes are never SQL values, session values,
logs or repository files. Historical DB rows contain only object key, SHA-256 and size.
Historical image retrieval reauthorizes current scope, checks the
checksum and responds `no-store, private`; no public/presigned URL is exposed.

## Schema / validation

Three additive tables: `staffing_employee_record_settings`, `staffing_employee_records`,
`staffing_employee_record_events`. The approved explicit schema-sync missing-table
bootstrap creates them in dependency order and installs immutability triggers as part
of table creation. The delivery compatibility step expands the finalization CHECK
to allow `delivered` while retaining `rts` and `signature` history. PostgreSQL replaces
the constraint transactionally; SQLite rebuilds only the record table with unchanged
rows, restored indexes/triggers and a foreign-key check. No columns/tables are added
for delivery. Repeated bootstrap is a no-op once upgraded; no historical row is edited.
Render's existing pre-deploy bootstrap must finish before readers start. No manual deploy.

Directory and history are paginated at 25; scope queries are set-based with no per-person
lookup. Record screens have no polling. Delivery uses a normal CSRF-protected form;
no signature pad or signature JavaScript is loaded.

Tests: `test_neostaffing_employee_records.py` and opt-in
`test_neostaffing_employee_records_postgres.py`; PostgreSQL accepts only disposable
loopback `neostaffing_test_*` databases, using isolated schemas. Browser proof uses
fixture employees only, not production personnel data.
