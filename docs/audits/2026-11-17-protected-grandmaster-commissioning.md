# Protected NeoApps Grandmaster commissioning

Starting main/LIVE: `9a67645aff530613f4b9c02f05382b78c02e83c7`. The separate inactive Phase 2 candidate `e866c2d61b643b1c4ccbc34ebe9bcf9767e4d133` is preserved for integration after owner commissioning. This release adds the missing action; it does not automatically promote anyone or activate the Phase 2 authorization foundation.

## Authorized action and safeguards

Account details now display the actual NeoApps platform role and a distinct **PROMOTE TO NEOAPPS GRANDMASTER** action, visible only to an eligible active, email-verified platform Grandmaster for another eligible account. It is independent of application and NeoNode role forms. Direct path for the verified backup: `/admin/users/36/commission-grandmaster`.

The confirmation page shows stable User ID, name, verified registered email and current platform role. It requires the exact target ID, an explicit platform-wide-authority checkbox, a bounded reason and the initiating administrator's current password. No credential is prefilled, returned in an error, persisted in an audit or logged. CSRF, authentication, existing database-backed login-attempt throttling and normal private-page headers remain in force. Invalid password attempts are recorded through the existing abuse-control service.

A signed ten-minute review binds the actor/target IDs, current account security state and the target's application/membership/node grant records. It is a conflict token, not authorization. POST rechecks fresh actor and target under the existing administration serialization and account row locks, validates the review, explicit confirmation and current password, and rejects self-directed, forged, stale, unsupported, duplicate or concurrent changes. Scoped Grandmaster roles and platform Master cannot satisfy the initiating platform Grandmaster requirement.

MFA flags are not treated as completed MFA. Accounts with MFA required/enabled are denied by this password-only action; nothing clears or changes those flags. Forced-password-change/restricted/inactive/unverified accounts are also ineligible. A real MFA challenge remains outside this release.

The authorized service changes only the target's platform role and authentication session version, invalidates old sessions and revokes outstanding password-reset/email-verification links. It preserves identity, password, MFA/recovery restrictions and every existing application/node grant. One `platform_grandmaster_commissioned` security event records actor, subject, timestamp, reason, explicit platform scope, before/after role/session versions, password-step-up method and matching grant fingerprints. The caller commits the operation; any failure rolls back role, sessions, token claims and audit together.

Ordinary peer promotion/demotion and account-conversion protections remain intact. No demotion action is added. Removing verification from the last active, verified, non-vendor platform administrator with supported credentials is now blocked in the existing email-edit flow. Concurrent email corrections cannot invalidate both remaining recoverable administrators. An unavailable-MFA or forced-recovery account is not silently counted as a healthy replacement.

## History and bootstrap

The existing Phase 1 security ledger gains idempotent SQLite/PostgreSQL UPDATE/DELETE guards, plus PostgreSQL TRUNCATE protection, including bulk SQL. No new account table, column, role migration or historical backfill is needed for commissioning. Trusted database-owner DDL is outside the immutability threat model. Bootstrap creates guards without changing existing account security settings or grants.

## Validation before release

| Focused check | Result |
|---|---|
| Python: commissioning, Phase 1 repairs, bootstrap, auth/account flows, revocation, permission rules, Smart Login and CSRF | **198 passed; 316 subtests; zero failed/skipped** |
| PostgreSQL: commissioning plus existing recovery, revocation, last-admin, bootstrap and vendor/Sort races | **31 passed; five subtests; zero failed/skipped** |
| Chromium desktop 1280×900 and mobile 390×844 | **One workflow passed**, exercising rejected password, explicit successful commissioning, visible actual platform role, one audit and preserved scoped grants |

All test mutations used synthetic SQLite or newly isolated schemas on the preserved loopback PostgreSQL server. External providers were mocked for the browser fixture. No test was deleted, broadly skipped, weakened or marked expected-failure; existing warnings remained visible. Initial fixture/browser timing failures were corrected: multi-client login state needed isolation, expiry tests needed to expire only the review token rather than the whole session, rate-limit tests used the canonical configuration keys, and browser submission now waits for actual navigation. These corrections did not weaken application guards.

Evidence: local ignored `instance/commissioning-release-python.log`, `instance/commissioning-release-postgres.log`, `instance/commissioning-browser-final.log`; screenshots/synthetic browser database under `instance/browser-evidence/commissioning/`. Python compilation, Jinja parsing and diff checks accompany the release receipt.

## Production baseline and staged release

Read-only Neon queries verified User 1 is the active verified platform Grandmaster and User 36 remains the active verified platform Watcher; both have no forced-password-change or MFA flags blocking the supported step-up. Brad's NeoStaffing Grandmaster and seven node Grandmaster grants are retained. They exceed his earlier scoped baseline and are not required to create a platform backup; operational necessity still needs owner review. Nothing is automatically revoked.

All five inherited Simulator pairs remain: User 3, gateway/membership 1/3, source app-access ID 7, nodes 1/2/4/5/7 (MotherBrain, Sektor, Reptile, Ermac, Rain). No explicit grant is inserted or migrated. The seven historical node-change audit records are retained; their pre-release digest is recorded locally for comparison.

The configured RFD night Sort starts at 21:00 Central. Pre-release review occurred before that window. Raw globally unreleased historical Work rows are not interpreted as evidence of current fueling or cleaned up. The existing Render main-branch AutoDeploy, pre-deploy bootstrap and worker settings are preserved.

Release sequence:

1. Deploy commissioning independently from current main after successful checks; verify exact SHA LIVE, bootstrap/worker startup, health/login/security headers and authenticated confirmation page using GET only.
2. Leave the final protected action to User 1. Codex does not enter the owner's password or submit the production promotion.
3. After the owner completes it, read-only verify Users 1 and 36 are active verified platform Grandmasters, the new actor-1/subject-36 platform audit exists, its grant fingerprints match, and the five Simulator pairs remain unchanged.
4. Only then integrate and validate the preserved inactive Phase 2 foundation, deploy it through main, and verify its additive empty tables/guards without changing grants or enabling a new authorization model.

Exact SHAs/deployment IDs and post-release receipts are recorded in the final delivery and ignored local receipts to avoid additional documentation-only deployments. Until step 3 succeeds, commissioning status and final Phase 2 completion remain pending owner action. No Phase 3, NeoBid cleanup, account conversions, operational mutations, Issues or temporary workflows are included.

## Changed files

* `app/auth/routes.py`
* `app/services/grandmaster_commissioning.py`
* `app/services/account_security_history.py`
* `app/services/database_bootstrap.py`
* `app/templates/auth/user_detail.html`
* `app/templates/auth/commission_grandmaster.html`
* `tests/test_grandmaster_commissioning.py`
* `tests/test_grandmaster_commissioning_postgres.py`
* `tests/browser/test_grandmaster_commissioning.py`
* This report.

All unrelated/untracked artwork, audits, prototypes, synthetic data and existing jobs are preserved.
