# NeoStaffing employee transfers and optional Seasonal seniority — 2026-11-04

Starting main: `503f6d8d4db511e7f05e746b93aa80dccbc4cc85`.

The existing shared employee editor now has TRANSFER for active Work Area placements. A compact confirmation dialog identifies the current assignment and groups destinations by Sort/Operation/Department. Combo employees explicitly select which assignment to transfer; other Sort placements remain intact. Unsaved profile/flow edits must be saved first and remain visible if transfer is blocked or cancelled.

The new transaction locks and refreshes actor authority and the employee, checks both profile and assignment revisions, validates/locks destination ancestry, and rejects duplicate Sort placements. It deactivates the old assignment and creates the replacement while retaining the same employee ID, personal fields and linked User. Old assignment rows, attendance and timecards are preserved. Leaving Shift uses the existing lifecycle to remove stale flow; entering Shift uses the explicitly selected Home and canonical flow defaults. Optional unknown flow details remain unset rather than invented. Transfer and profile-edit races accept one revision; stale requests cannot overwrite the winner. A repeated old transfer cannot create another placement.

Seasonal seniority may be NULL. Other classifications still require a valid date, including when changing from Seasonal. The database CHECK enforces this independently of forms. Existing dates are not changed or backfilled. Rendering, snapshots, bulk-change validation, sorting and exports handle missing dates; unknown dates sort last. Existing vacation eligibility remains unchanged.

The normal pre-deploy schema synchronization atomically relaxes PostgreSQL NOT NULL and validates the new CHECK, with an advisory lock and five-second lock timeout. It performs no employee UPDATE/DELETE. The SQLite maintenance upgrade preserves IDs, child foreign keys, indexes and triggers, rolls back DDL failures and refuses to consume a pending employee transaction. No hosting setting or credential changes are required.

Validation uses synthetic SQLite/PostgreSQL, mocked providers and a loopback-only network guard:

* Consolidated Staffing, retained-history, permissions, reporting, vacation, recovery, CSRF and CSP: **287 passed**, 352 passing subtests, no skips.
* PostgreSQL transfer/revision/profile races, rollback, schema upgrade, duplicate placements, CSRF and revocation: **16 passed**, five passing subtests, no skips.
* All tracked JavaScript plus new Seasonal/confirmed-transfer cases: **405 passed**, no skips/cancellations.
* Shared editor/retained-timecard/new transfer browser acceptance: **7 passed**, six passing viewport subtests. Chromium and WebKit cover 320/390/1440px, cancellation, dirty drafts, Shift→Deice→Shift and undated Seasonal creation.
* Final browser asset recheck, compilation, JavaScript syntax, preservation hashes and `git diff --check` are required before commit.

Initial failures and final logs/XML are retained under `instance/employee-transfers-20261104`. One new PostgreSQL assertion initially used title case instead of the canonical first-name normalization; its assertion now checks the actual winning profile/placement explicitly. A new CSRF fixture initially omitted `CSRF_PROTECT_TESTING`; the final test enables the actual protection. A standalone JS harness exposed a closure dependency in POST handling; explicit transfer confirmation now selects its uncertainty handling without that dependency. No existing test or operational guard was weakened.

Screenshots are under `instance/employee-transfers-20261104/screenshots` and `browser-regression`; they contain synthetic records only. No production employee/timecard transfer, creation, repair or deletion is used for verification. Unrelated artwork, prototypes, jobs, histories and existing synthetic database directories are preserved. Release is a focused main push and existing Render AutoDeploy; exact final SHA, LIVE deployment ID, bootstrap/health/security and served-asset verification are supplied in the completion receipt.

Final release recheck: both browser methods passed with six viewport subtests (33.64 seconds). One earlier default five-second visibility wait expired while a request was still pending; the acceptance check now waits for the specific transfer POST, requires HTTP 200, and uses a finite 15-second bound before checking the same UI/data assertions. Compilation, syntax, all 405 JavaScript cases and diff checks pass.
