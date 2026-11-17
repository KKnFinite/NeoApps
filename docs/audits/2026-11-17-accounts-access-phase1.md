# NeoApps Accounts & Access — Phase 1 security repairs

Scope: C1, C2, C3 and H1 from the local accounts/access redesign audit. No Phase 2 implementation, role migration, NeoBid cleanup or account conversion was performed in production.

Starting local/origin/main SHA: `d10b6b04425170b8bc35bc151103559f33e87b84`. Existing LIVE deployment before this release: `dep-dbub9cqjnfac73ba15i0`, same SHA. Workspace: `Z:\DevProj\NeoApps`.

## Repairs

### C1 — scope-bound policy management and protected platform authority

The old MotherBrain-authorized editor accepted permission IDs from every application and the platform. It could lower a platform threshold and then use that lowered threshold to acquire account-management powers.

The existing editor now filters its rendered rules and independently validates every submitted rule against the administrator's scope. A node Grandmaster can manage that node's operational rules; unrelated node or platform rules are rejected. A platform Grandmaster retains parent-scope oversight. An application policy scope requires the existing NeoApps Master plus that application's Grandmaster combination; this does not introduce the future application-account administration UI/model.

Platform security permissions have a server-enforced Grandmaster boundary regardless of permissive legacy database thresholds. Portal viewing and requesting additional app access retain their ordinary-user behavior. Platform-security and permission-editor thresholds render as locked Grandmaster controls, excluded from ordinary form submissions. Their saved legacy rows are not rewritten on GET or as a side effect of saving an operational rule. Forged threshold downgrades fail, and mixed valid/forbidden submissions roll back together.

**Intentional authority restriction:** production read-only inspection found platform-management thresholds configured as Master. Those rows remain intact, but they can no longer delegate platform-wide account/security powers below the protected boundary. Existing application operations are unchanged; Masters can delegate lower roles only in their own authorized node. This is security containment, not the future scoped-role migration.

### C2 — new-account initialization only

Bootstrap previously reset email/verification, password-change restrictions and MFA state on an existing bootstrap identity. Existing accounts are now left unchanged, including password/hash, username/email, active/global role, verification, session version, temporary-password expiry, forced/policy password changes, MFA secret/enrollment and recovery metadata. Existing missing/revoked grants are not recreated as a startup recovery action. New identities still receive the established first-account initialization.

Recovery is explicit: shared self-service recovery or the existing authenticated platform-Grandmaster emergency-reset action, with its reason, expiring temporary password, forced change and session invalidation. Emergency reset now also uses the account lock, revokes outstanding recovery/verification links and records a transactional security event. Rebooting or changing bootstrap environment variables is not a recovery procedure. A total platform lockout or MFA recovery requires owner-authorized maintenance; no automatic MFA reset/backdoor was added.

### C3 — atomic email correction, token invalidation and audit

An administrative email correction now invalidates outstanding password-reset **and** email-verification tokens, clears verification for the new address, rotates the subject's authentication session version, and records actor/subject, time and old/new email in the same transaction. User ID, password hash and legitimate app/node entitlements remain unchanged. Saving the same email again does not repeat the audit or session rotation.

Both token types are issued and claimed under the subject-account lock. Claims re-read the account/token after any wait. An old-address recovery lookup waiting behind an email correction cannot issue another link. Verification cannot use an old link to verify the replacement address. A valid new-address verification link still works. Failed edits roll back identity, tokens, session version and audit together.

The legacy test that explicitly preserved an old reset token after an email change now requires its invalidation.

### H1 — bounded delegation and administrator recoverability

The hierarchy remains Watcher < Operator < Simulator < Master < Grandmaster. No new role names, levels or grant store were introduced. Authority no longer uses the maximum of unrelated platform and MotherBrain ranks.

The existing node-role endpoint permits a Master to assign at most Simulator in their own node. It checks both existing and proposed roles, including inherited higher roles when an explicit row is absent. It rejects foreign-node changes, higher/equal-target demotion, self-promotion and self-reactivation. A node Grandmaster cannot commission a peer. Platform Grandmaster oversight of a different user's child-node grant remains available; this does not create a peer platform Grandmaster. Ordinary account controls cannot convert a protected platform Grandmaster.

Administrative writes refresh actor authority after taking the existing grant-serialization lock, and recheck after waiting. Last-admin counting excludes explicitly revoked app grants while preserving valid legacy approved memberships with no app row. Concurrent removals cannot remove both remaining effective node administrators.

Account conversion retains its existing **Sort → grant/account** lock order. Its final platform authorization is rechecked inside that order. A real PostgreSQL HTTP race verifies that an active Sort/asset operation can acquire its User lock without deadlocking against conversion. No production conversion was run.

## Persistence and compatibility

One additive table, `account_security_events`, records email changes, emergency resets, policy changes and direct node-role changes transactionally. It contains IDs, action, timestamp and bounded change details; no passwords, raw tokens or MFA secrets. ORM updates/deletions of these records are rejected. There is no new public history endpoint. The existing `db.create_all()` deployment-bootstrap path creates it; no existing account/grant/token columns are changed and no history is backfilled or invented. SQLite and disposable PostgreSQL bootstrap paths were exercised.

The implementation reuses the existing permission evaluator, request-scoped role projections/caches, role models, token hashes, account locks, vendor conversion service and CSRF/session guards. Scoped role-page reads do not provision unrelated missing grants. The general legacy inheritance system remains for a later approved migration.

## Files in this release

Application:

* `app/auth/routes.py`
* `app/models/__init__.py`
* `app/models/user.py`
* `app/models/account_security_event.py`
* `app/services/account_security.py`
* `app/services/database_bootstrap.py`
* `app/services/neoscorpion_account_types.py`
* `app/services/permission_rules.py`
* `app/services/user_tokens.py`
* `app/templates/auth/permission_rules.html`

Tests:

* `tests/test_account_security_phase1.py`
* `tests/test_account_security_phase1_postgres.py`
* `tests/test_auth_account_flows.py`
* `tests/test_authorization_revocation.py`
* `tests/test_database_bootstrap.py`
* `tests/test_neoscorpion_vendor_postgres.py`

Documentation: this report only. The earlier untracked redesign audit and unrelated artwork/prototypes/reports remain outside the release.

## Validation and evidence

Final release gates passed against the completed application patch before commit/push. Unique tests and passing subtests are counted separately.

| Gate | Result |
|---|---|
| Final focused Python: security repairs, auth, policy, bootstrap, reset atomicity, revocation, Smart Login, CSRF, headers and vendor workflow | **236 passed; 581 subtests passed; zero failed/skipped. 244.21 seconds.** |
| Final disposable PostgreSQL: token/email races, authority revocation, target role changes, last-admin removal, bootstrap, reset rollback and vendor/Sort races | **25 passed; two subtests passed; zero failed/skipped. 92.84 seconds.** |
| Python compilation and `git diff --check` | **Passed: 15 Python files compiled in memory; staged diff clean.** |

Evidence logs are local ignored files under `instance/phase1-release-gate-python.log`, `instance/phase1-release-gate-postgres.log`, and `instance/phase1-lock-order.log`. All mutations in these tests use in-memory SQLite or a newly created loopback-only `neostaffing_test_` PostgreSQL database. No broad application/audit inventory, heavy load test, test skip, expected failure or warning suppression was introduced. Existing deprecation warnings remain visible.

Initial failures were investigated: tests expecting bootstrap email rewrites or surviving reset links were corrected; administrative tests now use a separate authorized administrator instead of self-approval/reactivation; last-admin compatibility was repaired to distinguish a missing legacy app row from an explicit revocation. Existing operational assertions remain.

## Read-only production baseline and release verification

Render workspace was explicitly confirmed as My Workspace (`tea-d79io9p4tr6s73cuk1bg`). Service: NeoApps, `srv-d8buq2mgvqtc73e375j0`, AutoDeploy on `main` commits. Existing browser-session inspection confirmed the platform-Grandmaster-only Portal navigation and successful administration access. Fuel Dispatch showed **NO CURRENT SORT** during the pre-release inspection, outside the configured night Sort period. No production forms were submitted.

The Neon connector still returned `Tool list_projects not found`; no production database query, account count, owner-ID inventory or database alteration was performed through it. The authenticated platform path was verified independently through the existing session. No claim of a complete production IAM inventory is made.

The release commit is the commit containing this report. Its exact LIVE SHA/deployment and subsequent read-only checks are recorded in the local `instance/phase1-release-receipt.json` and final delivery, avoiding an additional documentation-only deployment. Release verification checks bootstrap/pre-deploy completion, worker startup, health/login, security headers, CSRF form availability and authenticated NeoGateway/NeoStaffing/NeoScorpion/account-policy pages using GETs only, followed by rollout errors/5xx/OOM/worker-timeout review. No synthetic production recovery, email edit, role change, approval or conversion is used to demonstrate the repair.

## Remaining boundaries

This release does not implement Accounts & Access, app-request history, NeoBid cleanup, preferred-app settings, supervisor/company models, broad identity validation, MFA challenges, a full scoped-role migration or a comprehensive administrative audit UI. Existing MFA flags remain enforced and preserved; usable enrollment/recovery is a separately reviewed concern. Legacy database policy values are retained even where a protected runtime floor now overrides them. No claim is made that every issue in the redesign audit is fixed.

**Stop after Phase 1.** No Phase 2 work is authorized by this release report.
