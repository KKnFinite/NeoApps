# Phase 3 — Accounts & Access

Baseline: `103787711173e2f5aa6d7c4941fb316b970d9149`. Source was fetched and matched origin/main before editing. The original redesign audit and Phase 2 completion report were reviewed. Unrelated untracked files, existing jobs and synthetic databases were preserved.

## Scope and architecture

The new workspace uses the current `permission_rules.user_can`, Phase 1 account-security guards, existing application/membership approval helpers and existing protected commissioning. It does **not** activate `AccountScopedRole`, change `account_authority` decisions, call dormant fulfillment, convert production accounts, seed grants, retire NeoNode Grandmasters or introduce delegated administration. Phase 2 request/decision tables now hold new request attempts and immutable decisions; live entitlements remain `PortalAppAccess` and existing gateway/node records.

### Workspace

* `/accounts`: searchable directory with application, status and account-type filters; 25 accounts per page, deterministic ordering and batched grant summaries.
* `/accounts/requests`: unified registration/additional-app review, status/application/search filters, pending counts and pagination. Legacy application and gateway-only requests are included without creating history on GET. Available-app filters exclude NeoBid. Existing entitlement summaries are distinguished from new durable attempts.
* `/accounts/security`: platform-Grandmaster-only oversight and paginated immutable ledger. Permission management remains backed by the existing scope-protected editor; the workspace route retains the same protections.
* `/accounts/people/<id>`: Identity, Access & Roles, Security and History. Identity, one application's access and one node's role save independently. Security recovery, account conversion and platform commissioning remain distinct protected operations.
* Safe legacy directory, request and profile GETs redirect into the workspace. Existing mutation endpoints retain their authorization/lock order. Their canonical approval/denial helpers close the same pending Phase 2 attempt, so compatibility writers cannot leave an already-decided request open. Node-scoped operational administration remains available to existing authorized node administrators; no new platform directory authority is delegated.

New profile writes use a signed, expiring actor/session/target/state review. The state fingerprint reads persisted identity, grants, memberships, requests and preferences, so legacy writers' changes also invalidate an old review. After the administration lock, authority and target state are freshly checked. Conflicts return HTTP 409 with escaped unsaved entries and a link to re-review; they do not turn stale draft input into a fresh actionable form. Exceptions roll back the complete action. Access changes invalidate the affected sessions. Profile GETs no longer seed missing node/application grants; the current legacy read projection is retained.

### Registration, requests and application switching

Registration keeps shared password policy, duplicate-identity protection, CSRF, rate admission and email verification. The responsive form requests multiple available apps, never roles. Native inline validation checks password confirmation and application selection. Server validation rejects unavailable apps and oversized/malformed fields before persistence.

My Account → App Access is available with zero approved applications and linked from shared desktop/mobile account navigation. Denied users can submit a justified new attempt while the existing denial remains effective. A snapshot preserves any actual legacy denial metadata when reapplying, marked as observed at request time rather than inventing an earlier attempt. Each new attempt's decision has its own immutable record, reason, actor receipt and timestamp. Duplicate open requests are rejected under the existing serialization lock and database uniqueness constraint.

Smart Login uses the same canonical approved/active/accessible app checks. The preferred application defaults OFF, is accepted only from currently authorized applications, and is ignored if access later disappears. The ordinary switcher shows authorized applications only. CryoCheck is an optional external link to the existing destination, defaults OFF, has no entitlement and never enters Smart Login's app count. Restricted Fuel Truck Drivers still land in their restricted workspace; their own read-only My Account provides password navigation but no app requests, preferences or administrative actions.

NeoBid is hidden from new signup/request/approval controls and rejected by shared server-side request/approval gates, including legacy entry points. Existing NeoBid grants and the owner's development access are not cleaned up or changed.

### Additive persistence

One new `account_preferences` table has a user primary/foreign key, nullable constrained preferred app, default-false CryoCheck visibility and positive version. Existing explicit bootstrap `create_all` creates it idempotently; no existing account columns or rows are changed. Reads do not initialize preference rows. Phase 2's existing request, decision and shared immutable security ledger are reused. No schema migration, history reconstruction, role backfill or operational data write is performed by deployment.

## Validation and release evidence

Focused runs cover registration, real approval/denial/reapplication, independent identity edits, old-token invalidation, stale reviews, request rollback, protected administrator recovery, current authorization revocation, scope isolation, account conversion, restricted-driver operation, Smart Login, CSRF and security headers. PostgreSQL tests use preserved disposable loopback schemas. Browser fixtures use synthetic SQLite databases and mocked external providers; no production signup, email, conversion or operational write is used.

Final focused results:

| Check | Executed result | Local evidence |
|---|---|---|
| Combined Python HTTP/security/compatibility suites | **223 passed; 386 subtests; zero failed/skipped**, 280.20 seconds | `instance/phase3-python-final.log` |
| Additional platform/scoped policy boundary test | **1 passed**, distinct from that combined run | `instance/phase3-policy-oversight.log` |
| PostgreSQL requests, commissioning, Phase 1/foundation and vendor concurrency/rollback | **38 passed; 11 subtests; zero failed/skipped**, 299.96 seconds | `instance/phase3-postgres-verified.log` |
| Chromium/WebKit registration, review, reapplication, preferences, Smart Login and account conversion | **1 test / 4 viewport-engine subtests passed**, 47.17 seconds | `instance/phase3-browser-release.log` |
| Existing protected-commissioning desktop/mobile workflow | Passed in the compatibility browser run; no automatic production commissioning | `instance/phase3-browser-verified.log` |
| Python/Jinja parsing, JavaScript syntax and diff checks | Passed | Local command output |

The combined Python plus distinct policy check cover **224 unique cases**, not 224 plus the subtest counts. Earlier failed runs are retained as evidence: new fixtures omitted a mandatory vendor actor and CSRF setup; retired page assertions needed canonical workspace URLs; one browser helper selected a hidden legacy logout; WebKit exposed a test that checked the unchanged URL before a save redirect completed. The final browser checks explicitly wait for each completed save and verify persisted decisions. No authorization guard was weakened to resolve these failures. Existing deprecation warnings remain visible and were not suppressed.

Screenshots are retained in `instance/browser-evidence/accounts-phase3/`: `registration-<engine>-<width>.png`, `profile-<engine>-<width>.png` and `my-account-<engine>-<width>.png`, for Chromium/WebKit at 1280 and 390 pixels. The actual images were inspected; forms fit the viewport without horizontal overflow. A platform-only header prevents operational chrome from overlapping the new workspace. Existing application mobile drawers retain their behavior and add My Account navigation. Synthetic browser databases remain preserved; `latest-fixture-path.txt` records the local temporary fixture used to avoid network-drive SQLite fsync overhead.

Exact release SHA/deployment and subsequent read-only production checks are recorded in `instance/phase3-live-receipt.json` and final delivery after Render reaches LIVE. A commit/push alone is not deployment verification. No failed tests are skipped, suppressed or marked expected. Retired markup assertions are replaced with current controls and real outcomes; permission, token, concurrency and lifecycle assertions remain.

## Production preservation baseline

Read-only inventory before release confirmed active verified platform Grandmasters User 1 and User 36, session versions 1 and 3. The original eight security events have digest `aa4da678a8cb49b058118d21f4711447`. Independent application/node digests for both administrators match the Phase 2 receipt. All five User 3 inherited Simulator pairs remain absent as explicit rows and effective under their unchanged source grant. Staged roles, request attempts and decisions each contained zero rows before release. NeoBid had seven pending grants; they are retained, not processed by this phase.

## Deliberate limits

* New scoped-role enforcement, permission migration, new delegated administrators, vendor supervisors and NeoNode Grandmaster retirement remain out of scope.
* MFA is not represented as operational; accounts requiring an unsupported MFA challenge remain blocked by existing protections.
* Legacy pre-history remains unknown where it was never recorded. Current legacy records are displayed honestly, without fictional audit timestamps.
* History panels bound the latest 40 entries; the shared platform ledger is paginated and no historical rows are removed.
* Legacy high-risk reset/commissioning and scoped policy forms retain their specialized security workflows. This phase does not invent a universal broad account-edit transaction.
* Brad's existing application/node Grandmaster grants remain for owner review; none are automatically revoked.

Phase 3 stops at this release. Phase 4 is not started.
