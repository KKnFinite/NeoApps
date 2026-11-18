# Phase 4 — Fuel Truck Driver and Vendor Supervisor

Baseline: `f384b535672fecd2c4b98c40626f672034a2f7ac`. Fetched origin/main before editing and again before release; both matched. The original Accounts & Access audit and Phase 3 completion report were reviewed. Phase 4 uses current authorization, not the inactive scoped-role engine.

## Implemented

* Shared Create Account has a Fuel Truck Driver account intent. UPS ID is mandatory on both client and server; only restricted NeoGateway access can be requested. Signup does not provision vendor participation, company membership or Supervisor authority. Email verification and administrator review remain separate prerequisites.
* Accounts & Access supports verified UPS ID correction during review and afterward, without converting or recreating the account. IDs retain leading zeros. Normalized duplicate IDs are rejected. Signup and canonical ID editing share a short PostgreSQL transaction advisory lock for uniqueness across concurrent writers. Existing user IDs, passwords and operational references are retained.
* A correction cannot silently unlink an existing NeoStaffing person or link a different person. An existing matching person requires explicit Person ID confirmation; changing an established person link stops for protected review. No staffing records are rewritten.
* New identity/company actions require fresh active platform Grandmaster authority under existing administration serialization. Signed actor/session/target/state reviews reject stale changes. Supervisor capability changes additionally require current-password reauthentication, explicit account-number confirmation and an attested reason. Unsupported MFA remains fail-closed; no MFA capability is invented. Affected sessions are invalidated. The existing immutable security ledger records the change in the same transaction.
* Visible account/menu labels now say **Fuel Truck Driver**. Existing identifiers, URLs and historical audit action codes remain compatible.
* **Send NeoApps Signup Invitation** sends the shared signup URL. It creates no user, password, token or permission. Its bounded rate admission has a separate action key from public signup. Email delivery releases database locks first, does not automatically retry, and records a hashed recipient receipt without putting addresses or credentials into logs. Legacy registration, recovery and invitation URLs retain their existing shared-authentication compatibility redirects; retired account/password provisioning controls are not restored.

### Nightly participation

The driver selects the current Sort and requests a truck before participation approval. The waiting page exposes only the current Sort, eligible truck identifiers and that driver's own claim. It does not assemble or expose full mission/Dispatch data. A pending request does not connect a truck, select a new inventory baseline or approve participation.

Dispatcher **APPROVE FOR SORT** checks the pending request version and, where needed, the nightly approval version under the existing Sort lock. Approval and connection commit together. A downstream conflict rolls back approval, connection and audit writes. Duplicate decisions are rejected. Existing preapproved drivers, independent nightly controls, inventory verification and canonical physical fueling remain compatible. Denied/revoked drivers cannot claim, and Sort rollover invalidates stale requests. Existing read-only mission listings remain available after approved connection.

### Verified companies and Supervisor View

Company affiliations are explicit, administrator-attested and dated. Unknown affiliations stay unknown. A Supervisor is a restricted account with a separately assigned read-only capability, never a signup choice. Granting Supervisor while current driver participation, a connection or a pending claim exists is blocked until Dispatcher release.

Supervisor HTML and revision endpoints independently verify current account, UPS proof, membership, active verified company and gateway. They select only current-Sort drivers from that company and their permitted truck/assignment fields. Mission projection contains tail, flight, authoritative parking, destination and departure time; it excludes tank readings, gallons, internal notes, employee IDs and other companies. Caller-supplied company/Sort parameters cannot select a different scope. Supervisor accounts cannot claim trucks, submit fuel readings, obtain Dispatcher controls or perform driver mutations. Company deactivation/revocation denies live reads; the existing polling implementation clears the old view. No new refresh system or authorization cache was introduced.

Converting a restricted account back to full NeoApps closes its current company membership and appends a security audit. Converting it to a restricted account again cannot resurrect an old Supervisor capability. Historical affiliation remains readable; a new company/capability decision is required.

## Additive persistence and compatibility

Three new tables are created by existing explicit bootstrap `db.create_all()`:

| Table | Purpose and safeguards |
|---|---|
| `fuel_driver_identities` | Account intent and attested UPS ID; user primary/foreign key, proof actor/time/reference and version. |
| `fuel_vendor_companies` | Gateway-owned verified company, normalized unique name per gateway, active flag and version. |
| `fuel_vendor_memberships` | Dated person/company association and Supervisor capability; one current membership per user through a partial unique index, date consistency constraint. Prior memberships remain recorded. |

No existing columns, grants or affiliations are migrated or backfilled. No historical fueling/event/inventory rows are rewritten. Existing account-history triggers seal the shared security audits. Repeated schema creation is idempotent; failed mutations roll back user, proof, session and audit changes. Older application versions can coexist with these additive tables. No scoped-role cutover, NeoBid cleanup, node-role retirement or permission relocation is performed. FOB → UPLIFT remains on hold.

## Production identity evidence and owner-directed test account removal

Read-only inventory confirmed active verified platform Grandmasters User 1 and User 36, with unchanged session versions 1 and 3. The five inherited User 3 Simulator mappings remain legacy/inherited and are not converted into explicit grants.

The only legacy restricted account was User 55, missing UPS ID/company proof, with zero claims and zero connections. The owner identified it as their disposable test account and explicitly authorized removal. Existing authenticated Dispatcher **DISABLE ACCOUNT** removed its active driver access; it did not hard-delete the identity or its history. Read-only verification confirms `enabled=false`, zero claims/connections, six retained vendor audit rows, and an `account_disable` receipt by actor 1 at `2026-11-18T03:39:30.868Z` (five prior entries plus that receipt).

No company was inferred, fabricated or assigned in production. No production account conversion, Supervisor commissioning, signup invitation, fueling action, inventory operation or test mission was performed. The explicit test-account disable is the only owner-directed production change in this package beyond additive deployment bootstrap.

## Validation and evidence

All new operational fixtures are synthetic; email/external providers are mocked. PostgreSQL work uses isolated loopback schemas, retained rather than cleaned up. Browser fixtures use local synthetic SQLite files. Existing jobs, unrelated untracked work, artwork and databases were not cancelled, reset or removed.

| Check | Result | Evidence |
|---|---|---|
| Phase 4 plus shared authentication, Phase 1, commissioning, Accounts & Access and vendor operational regressions | **169 passed**, 254.607 seconds | `instance/phase4-python-final.log` |
| Final account-conversion and latent-Supervisor regression | **12 passed**, including **one additional unique case** (170 unique Python cases overall) | `instance/phase4-conversion-final.log` |
| Additional final negative Supervisor/active-driver boundaries | **4 passed** (same cases as above, strengthened assertions) | `instance/phase4-boundaries-final.log` |
| PostgreSQL new identity/company/claim races, rollback, prior foundation and Phase 1/commissioning security | **29 passed**, 62.613 seconds | `instance/phase4-postgres-final.log` |
| Existing PostgreSQL duplicate-claim, competing-driver and completion/inventory races | **3 passed**, 28.175 seconds | `instance/phase4-existing-operations-postgres.log` |
| Final identity/company PostgreSQL rerun including Supervisor-grant versus conversion race | **6 passed**, including **one additional unique case** (33 unique PostgreSQL cases overall) | `instance/phase4-conversion-postgres.log` |
| Existing JavaScript dirty-input/deferred-refresh/revocation contract | **1 passed**, plus both changed scripts' syntax | `tests/js/neoscorpion_vendor.test.cjs` |
| Chromium and WebKit, 1280px desktop and 390px mobile | **1 test / 4 engine-viewport scenarios passed**, 37.541 seconds | `instance/phase4-browser-live-ready.log` |
| Changed Python compilation, Jinja parsing and diff whitespace | Passed | Local command output |

Browser scenarios exercise real CSRF-protected signup, email verification, administrator UPS verification, restricted approval, preapproval truck request, Dispatcher approval, live transition without manual reload, company-scoped Supervisor View, no driver controls, live revocation, missing-CSRF rejection and horizontal-overflow checks. Actual screenshots were inspected. Evidence is in `instance/browser-evidence/accounts-phase4/{pending,approved,supervisor}-<engine>-<width>.png`; the fixture location is recorded alongside them.

Earlier failed runs are retained: the new PostgreSQL fixture initially omitted the required creator FK, browser administration initially lacked its operational node scope, and the browser fixture reused an expired ORM company outside its request. These were fixture corrections. Existing authorization and concurrency guards were preserved. Existing deprecation warnings remain visible.

### Separately reproduced baseline test debt

The additional `test_authorization_revocation.py` suite executed 24 unique cases: **19 passed and five failed** (four assertion failures plus six NeoBid subtest error events belonging to one case). These five IDs reproduce against a separately preserved, unchanged baseline source snapshot; they are not Phase 4 regressions:

* `test_access_request_state_matrix_and_preserved_metadata`: assumes NeoBid requests are available; current prelaunch guard rejects six variants.
* `test_authenticated_admin_forms_show_disabled_and_require_deliberate_selection`: expects a retired administrator page to return 200 rather than the Accounts & Access redirect.
* `test_disabled_apps_enter_existing_administrator_queue`: expects the old 302 response; the current compatibility route uses 303.
* `test_disabled_membership_reinstatement_enters_queue_without_authority`: expects legacy membership mutation; current request history preserves the inactive entitlement while a request is reviewed.
* `test_self_service_ignores_forged_role_and_gateway_fields`: stops at the obsolete 302 versus 303 assertion.

Evidence: `instance/phase4-python.log` and `instance/phase4-existing-failures-confirmed.log`; the latter executes all five against committed baseline `f384b535672fecd2c4b98c40626f672034a2f7ac`. They are not deleted, skipped, relabeled expected failures or repaired in this package. The current request, forged-field and authority behavior is covered by the passing Accounts & Access/security tests. This is not a full-system pass claim.

## Release and limits

Release uses existing main AutoDeploy, with no infrastructure/hosting changes. Exact final SHA, deployment ID, bootstrap/worker evidence, health/login/security headers, committed asset hashes and authenticated read-only checks are recorded in `instance/phase4-live-receipt.json` after Render reaches LIVE and in final delivery.

Production has no verified company memberships to exercise as an authenticated Supervisor. That behavior is verified with synthetic isolated records, not fictional production affiliations. A platform administrator must verify a real company and each identity before assigning capability. Existing extra application/node Grandmaster grants remain untouched for owner review. The five inherited Simulator mappings and inactive authorization foundation are preserved. No role migration or Phase 5 begins automatically.
