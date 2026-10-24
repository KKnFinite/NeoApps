# NeoSubZero Release 4 — FROST Vault

## Boundary and storage contract

FROST is a private logical vault on the existing R2 provider, not a new provider,
subscription, public file server, prediction model, or operational spray database.
SPEAR fueling objects and exports are unchanged. New SubZero evidence uses
`frost-vault/v1/gateway-{id}/` with categories:

- `original-xchange`, `extracted-xchange`, `manifests`
- `spray-history`, `treatment-context`, `weather`
- `frost-observations`, `evaluations`, `training-datasets`

The latter categories reserve explicit versioned boundaries; this release does
not invent observations, evaluations, or training datasets. Operational spray
snapshots already carry their recorded weather, original tail/mission context,
participants and correction provenance.

Legacy `subzero-spray/v1/{gateway}/` and
`neofrost-treatment-context/v1/{gateway}/` objects remain in place. Existing
successful receipts are not blindly re-exported. The authenticated catalog reads
legacy and new objects with gateway and content-hash verification. No object is
retired, renamed, or deleted; source IDs, revisions and previous keys survive.

## Source ingestion

Settings links to `/neosubzero/frost-vault`. Import and retrieval reuse
MotherBrain's `neosubzero.settings.edit` rule and current gateway authority.
Original and extracted downloads are authenticated attachments, never public
object URLs. Uploads use existing CSRF enforcement. Paths, binary input,
malformed CSV, oversized files and wrong-gateway exports are rejected.

Supported input is the actual supplied UTF-8 Xchange deicing-log CSV schema.
The limit is 10 MiB / 25,000 records. Other CSV schemas retain their original
with an explicit unsupported status. PDF, XLSX and imagined export formats are
not claimed as supported.

The additive catalog records content SHA-256, original filename/length, importer,
actual import UTC time, source date range, parser version, extraction/review
status and immutable import audit. Gateway/hash uniqueness prevents concurrent
duplicate imports. Source corrections require a new content hash; originals are
not replaced. Duplicate attempts append audit entries without creating new
documents or operational transactions.

Original bytes are encrypted in durable database staging before provider I/O.
The worker writes an encrypted original, extracted JSON and provenance manifest,
then reads and verifies them before setting OFF-SITE VERIFIED and releasing the
staged blob. Partial writes are idempotently reconciled; outages retain staging.
No cloud I/O blocks spray, FIN or CLEAR.

Encryption uses a domain-separated key derived from the existing application
SECRET_KEY, with SECRET_KEY_FALLBACKS for rotation. Operators MUST retain these
keys/backups while encrypted evidence needs them; removing every applicable key
makes the originals unrecoverable. Use the existing private provider security
and backups. FROST evidence has no automatic expiry or public sharing.

Retries reuse the existing bounded, throttled, demand-driven archive worker.
SubZero/admin traffic, including normal catalog status refresh, wakes it; this
is not a new always-running queue. With no requests, retries wait for subsequent
traffic. Delivery errors remain visible to administrators. The first-import
manifest is immutable; subsequent duplicate-attempt audits remain in the
database audit catalog. Spray correction revisions retain the existing archive
reconciliation contract.

## Extraction quality and the supplied 2025–26 season

Parser: `xchange-deicing-log/v1`. Every source field and logical record order is
preserved, with source document/hash/record provenance. Type I and Type IV are
separate recorded contributions; missing quantities are not changed to zero.
An export row is not assumed to establish a shared aircraft pass identity.
Source Operator is not silently relabeled Flyer. Missing flight/surface fields
remain missing. ApplicationDate and local clocks remain separate because their
calendar-versus-operational-date relationship is not independently established.
Created/Modified UTC values are never substituted for spray timestamps.

The existing offline `neosubzero_frost_history` adapter is not used for this raw
import: its single-fluid row and combined date/local-datetime contract would
lose the supplied export's independent Type I/IV contributions and resolve an
unverified date convention. Its existing offline tooling remains unchanged;
FROST ingestion does not invoke its event grouping or training-label inference.
No second storage pipeline or operational spray model is introduced.

Supplied source verified locally:

- SHA-256: `cb26890bee895c63913ff6eda7a7b146e5496cb6185c850197c66a0c8a7a28cb`
- 1,381,372 original bytes; 3,053 source records.
- Source application dates: 2025-09-04 through 2026-06-25.
- Type I: 150,887.00 GAL; Type IV: 39,790.00 GAL.

The private source is NOT committed to Git. A spray/reason code is not a verified
frost observation. Every imported row remains frost-unknown and training-ineligible.
Ordinary unsprayed CLEAR/NEGATIVE states are not archived as frost-negative labels.

## Validation evidence

The Release 4 PostgreSQL fixture exercises canonical Alpha–Echo/Remote missions,
Pretreat, pad plus three waiting aircraft, two departure passes, mid-operation
truck/team changes, independent END snapshots, FIN/CLEAR without gallons,
negative/unsprayed CLEAR, late Driver gallons after reassignment, audited
post-CLEAR correction, tail attribution, 14 actual-pass archive comparisons and
sort rollover. Concurrent upload requests produce one catalog entry.

SQLite and PostgreSQL tests also cover populated-schema/idempotent bootstrap,
append-only audits, authorization, private downloads, source hashes, duplicate
imports, malformed/unsupported input, outage/partial-write retries and legacy
reads. Browser coverage uses Chromium/WebKit for uploads, failed-save recovery,
CSRF, authorization, private downloads, status refresh, and 320–1440px overflow;
existing Operations, Deicer/reporting and DUCC suites protect the operational UI.

The unchanged starting baseline had two unrelated NeoStaffing attendance errors:
`test_manual_callout_removal_does_not_restore_when_here` and
`test_not_here_attendance_suppresses_then_restores_selected_callout`.
They are not new FROST regressions. The Release 2 browser helper was repaired to
respect an already-expanded team section preserved by live reconciliation.

No production spray, staffing, inventory or mission data is used as test data.
