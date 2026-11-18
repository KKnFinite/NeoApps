# NeoApps Phase 5 — scoped authorization and owned administration

## Scope and release gate

Starting main and verified Render LIVE source: `d292c79fdfef9e133115a8a0790c73a351afc14d`, deployment `dep-dbuiaa2vcj2c738q8vn0`. Main was fetched and rechecked without resetting local work. This package does not launch/clean up NeoBid, change the queued FOB → UPLIFT work, convert production accounts, or change operational workflows.

Code deployment and data cutover are deliberately separate. The new code preserves legacy authorization until the protected, reviewed **whole** cutover commits. Startup and GET cannot activate it. After Render confirms the new code LIVE and old workers have exited, an authenticated recoverable platform Grandmaster reviews the exact mapping and reauthenticates on `/accounts/security/scoped-cutover`. No password is collected in chat. Final deployment/activation observations are recorded in `instance/phase5-live-receipt.json` and the delivery report. Do not interpret an installed but inactive feature as completed role migration.

## Phase 4's five authorization failures

The original `test_authorization_revocation.py` run reproduced **19 passing / five failing unique cases**, including six NeoBid subtest error events in one case (`instance/phase5-authorization-before.log`). All 24 now pass (`instance/phase5-authorization-after.log` and final rerun).

| Original case | Verified contract and correction |
|---|---|
| `test_access_request_state_matrix_and_preserved_metadata` | NeoBid is unavailable for new requests. Existing entitlement/decision metadata must remain intact. Updated the matrix to assert rejection and preservation, rather than permitting a prelaunch request. |
| `test_authenticated_admin_forms_show_disabled_and_require_deliberate_selection` | Legacy profile GETs redirect to the unified profile. Assertions now inspect current disabled selections and exercise the actual disable/restore POSTs. |
| `test_disabled_apps_enter_existing_administrator_queue` | The old request URL is a 303 compatibility redirect. The current signed My Account request creates a durable attempt without reactivating the entitlement. |
| `test_disabled_membership_reinstatement_enters_queue_without_authority` | Found a real self-service gap: an approved app with a revoked gateway membership could not request gateway review. My Account now offers that request. It changes neither the app nor membership authority. |
| `test_self_service_ignores_forged_role_and_gateway_fields` | Uses the canonical request endpoint and a signed account review. Forged role/gateway/node/status fields still cannot grant access. |

No case was deleted, skipped or marked expected failure. Historical decisions remain immutable.

## Read-only production inventory and exact mappings

Repeatable-read **READ ONLY** production transactions examined authorization metadata only. No passwords, names, emails, aircraft payloads or HR records were copied into the fixture/report. Source evidence: `instance/phase5-inventory-before.json`, `phase5-inventory-recheck.json`. Sorting by immutable row IDs confirms **zero changed security/grant fields** between those observations.

* 57 users: two platform Grandmasters and 55 platform Watchers.
* User **1** and User **36** remain active, verified, unrestricted platform Grandmasters with supported recovery and session versions **1 / 3**. The protected commissioning receipt remains audit **8**, actor 1 → subject 36. No identities were inferred from names.
* 102 app entitlements, 56 gateway memberships, 366 node grants, 159 stored permission rules; one active RFD gateway and seven active nodes. Zero staged scope roles before cutover.
* Fourteen node Grandmaster grants belong exclusively to those two platform Grandmasters. All map to **node Master**, retaining explicit node operations plus protected platform oversight. No platform/app role is increased.
* User 36's NeoGateway app remains **Watcher** and NeoStaffing app remains **Grandmaster**. The latter is redundant for his platform oversight but is preserved. User 1's existing app Grandmaster grants are also preserved. No unrelated app privileges are revoked or expanded.

| Account / membership | Exact existing node grant IDs | Reviewed replacement |
|---|---|---|
| User 1 / membership 1 | 1–7, nodes 1–7 | Grandmaster → Master, retaining active flags |
| User 36 / membership 36 | 637–643, nodes 1–7 | Grandmaster → Master, retaining active flags |
| User 3 / membership 3 | Missing explicit nodes **1, 2, 4, 5, 7**; source NeoGateway access 7 | Materialize exactly five **Simulator** grants; existing Scorpion/SubZero Master grants remain untouched |

Fifty-six explicit gateway-rank records are mapped from existing memberships. User 1's existing NeoGateway app Grandmaster authority maps to gateway Grandmaster. User 36 receives gateway Watcher, preserving his app rank; his platform oversight remains a separate capability. Other memberships receive Watcher, with eligibility/active flags preserved. No node role is promoted into platform or application authority.

The actual shared authorization engine was executed before and after a genuine synthetic cutover using anonymized production grant metadata: **9,063 decisions (57 × 159 × one gateway), zero differences**, zero differences from the pure review projection. All fourteen retirements and all five Simulator materializations were verified. Evidence: `instance/phase5-actual-inventory-final.log`, `phase5-actual-inventory-comparison.json`. The read-only batched adapter is primed before the legacy comparison, avoiding the old getter's implicit seeding during measurement. Domain predicates, such as Sort ownership, HR scope and vendor company, remain additional mandatory checks; the finite matrix does not claim to replace them.

Missing explicit app entitlements, non-platform node Grandmasters, staged candidates, inherited Grandmaster ambiguities, fewer than two recoverable platform administrators, or any unexplained decision difference block cutover. A newly changed production inventory invalidates the signed review.

## One authorization engine and explicit ownership

The existing `permission_rules.user_can` remains the permission evaluator. Phase 2's typed `Scope`/`Authority` adapter supplies shared administration decisions. There is no cross-user cache or union of old and staged permissions.

| Scope | Canonical rank source | Administration |
|---|---|---|
| NeoApps | `User.role` | Platform Grandmaster only; ordinary edits cannot commission/remove a platform peer |
| Application | `PortalAppAccess.role` plus approved/active entitlement | Platform Grandmaster, or **NeoApps Master AND that application's Grandmaster** |
| Gateway | Phase 2 `AccountScopedRole`, **gateway kind only**, plus approved app/membership | Platform oversight or an eligible NeoGateway/gateway Grandmaster |
| NeoNode | Exact `GatewayNodeRole` plus approved app, gateway and active node | Parent oversight or that node's Master; ordinary node Master delegates only through Simulator |

Watcher < Operator < Simulator < Master < Grandmaster is retained. Both the target's existing rank (including inactive tombstones) and selected rank are checked. No self promotion, peer promotion/demotion by lower authority, unrelated-role combination or vendor authority is accepted. New application/gateway Grandmasters require a separate parent commissioning screen, current-password step-up, explicit target/scope confirmation and immutable receipt. It does not also grant platform Master. NeoBid commissioning is blocked. Platform Grandmaster commissioning remains the existing protected procedure.

After activation, getters, lightweight/batch adapters and app approvals cannot seed missing node roles or revive revoked roles. App revocation gates every gateway, including non-default gateways whose legacy getter previously bypassed the app gate. Newly introduced nodes receive no automatic grants. Candidate staging/decision services close after cutover; candidate app/node/platform rows never become live role sources. Restricted-driver conversion and NeoGateway revocation also tombstone explicit gateway administration ranks.

The nightly Fueler eligibility projection was updated to use exact scoped thresholds and explicit node/app evidence after activation. A missing/inactive node grant cannot regain eligibility through an application-rank fallback. Existing physical work, assignments and evidence are not rewritten.

Shared provider/integration controls, API administration and gateway refresh settings remain protected at their parent scope. Platform account/recovery/delegation boundaries remain hard gates. Sensitive Staffing HR and permission-management policies remain protected; operational thresholds do not bypass existing HR/resource checks. All stored production thresholds are preserved. Their reviewed lower bounds are recorded in the immutable cutover receipt.

## Owned management UI and compatibility

Each owner has distinct **Permission rules** and **Role assignments** pages: `/neoapps`, `/neogateway`, `/motherbrain`, `/neoscorpion`, `/neosubzero`, `/neosektor`, `/neoermac`, `/neorain`, `/neoreptile`, `/neostaffing` followed by `/permissions` or `/roles`. NeoStaffing application roles/approvals are managed through `/neostaffing/accounts`; NeoGateway app approvals through `/neogateway/accounts`. Only the exact application-administration conjunction sees those controls. Node directories expose minimal names/account IDs, not emails, UPS IDs or HR profiles.

The current app/node's **Roles & Permissions** link appears in existing account/mobile utilities only for authorized administrators. Platform Security & Permissions provides central oversight and the protected cutover/rollback link. Scope switching lists only authorized scopes. Subject directories are searchable and paginated at 25 accounts. The existing responsive Accounts shell is reused. Legacy `/motherbrain/permissions` resolves to MotherBrain's owned rules after activation; platform aliases resolve to NeoApps; the existing Staffing permission URL resolves to Staffing's owned editor. Existing account/profile compatibility links and protected operations remain available.

Every mutation uses CSRF, fresh authorization under the shared administration lock, a signed current account/policy review, target locking, immutable before/after history, atomic rollback and request-cache invalidation. Role/entitlement changes rotate affected sessions. Policy overrides are bound to exact scope, so editing RFD Scorpion cannot modify another gateway's policy. Ordinary users cannot gain account administration by lowering a configurable operational threshold.

## Additive schema and controlled rollback

Two new tables are installed idempotently by existing explicit bootstrap:

* `account_authorization_states`: one constrained singleton, version, activation receipt, active flag and post-cutover inventory fingerprint. **No row is seeded on startup or GET.**
* `account_scoped_policies`: unique scope/key overrides with valid role constraint, version and security receipt. The existing `PermissionRule` table remains the preserved compatibility baseline.

Existing account/node columns and role constraints remain readable for historical/rollback compatibility. An active-mode ORM guard rejects new node Grandmaster grants. No data migration runs during bootstrap.

Activation serializes with existing account administration, rechecks actor/session, takes bounded PostgreSQL table locks (five-second lock timeout), validates the complete reviewed inventory, appends one receipt, maps all roles, and writes the active marker **last in the same transaction**. Failed or duplicate attempts cannot leave a partially migrated authorization model.

Rollback is an explicit password-confirmed action. It restores the fourteen former node Grandmaster ranks and legacy mode together only if no later grant/policy/security inventory changes occurred. It retains the five explicit Simulator records, immutable audit and inactive gateway-rank history. Subsequent legitimate administration blocks automatic rollback rather than overwriting newer authority. Code rollback must follow the data rollback; never deploy the old authorization code against an active retired-role mapping. A second cutover after rollback requires a separately reviewed procedure.

## Validation evidence

All databases and provider/browser mutations below are synthetic/local. Existing schemas, temporary fixtures, jobs, untracked artwork and unrelated work were retained.

| Check | Executed result / evidence |
|---|---|
| Original five failures and full authorization-revocation module | **24/24 pass**; initial 19/24 reproduced; `phase5-authorization-before.log`, `phase5-authorization-after.log`, final cutover regression log |
| Inactive foundation, Accounts workspace, request compatibility | **56/56 pass** in `instance/phase5-foundation-recheck.log` |
| Actual production-shaped engine comparison | **9,063 / 9,063 unchanged**; fourteen node retirements, five explicit Simulator grants, 56 gateway mappings |
| New scoped security tests | **20 cases**: scopes/ranks, delegation, protected peers, cutover/review/rollback, failed-final-switch rollback, policy floors/isolation, stale reviews, candidate closure, vendor/inactive exclusion, cross-gateway app revocation, nightly eligibility and read-only owned pages; `instance/phase5-final-ui-security.log` |
| PostgreSQL scope/cutover/policy races | **7 cases**, isolated retained loopback schemas; concurrent activation, role edits, approve/deny, actor revocation while waiting, policy edits, audit-failure rollback, rollback versus role mutation; final cutover regression log |
| Existing Phase 1–4 PostgreSQL security/driver suites | **33/33 pass**, `instance/phase5-postgres-regression.log`: recovery invalidation, protected commissioning, last-admin/revocation, immutable history and driver/company transactions |
| Focused shared compatibility batch | **253 pass / one baseline error**, 254 executed, `instance/phase5-python-regression.log`; shared authentication, Phase 1/2/3/4, permissions, SubZero, retained Staffing, Sektor and fueling submission |
| Chromium and WebKit, 1280px desktop and 390px mobile | **Four engine/viewport scenarios pass**, real CSRF forms: reviewed cutover, delegated role save, stale replay 409, owned policy save, app conjunction approval, cross-app 403 and missing-CSRF 400; no JS errors or horizontal overflow. `phase5-final-ui-security.log`; inspected screenshots under `instance/browser-evidence/accounts-phase5/` |
| JavaScript / syntax / whitespace | Existing vendor dirty-edit/deferred-refresh test passes; Accounts JS syntax, changed Python/Jinja parsing and `git diff --check` pass |

The new policy-race fixture initially used the Dispatcher rule whose baseline is Simulator, making one branch a no-op. It now uses the registered Fueler rule (Operator baseline) so both competing choices are real changes. Earlier failed logs remain available. Existing deprecation/resource warnings were not suppressed.

### Separate unchanged fueling test debt

`tests.test_neoscorpion_fueler_submission.SubmissionTest.test_required_target_increase_and_revert_preserves_submitted_source_for_review` sends `26` and `25`; the current Fuel Load contract requires one-decimal K-LBS strings. It errors before the downstream assertion. Re-executing with the service loaded directly from committed Phase 4 source produces the same error (`instance/phase5-submission-baseline.log`). No fueling validator/test is changed, skipped or weakened in this package. The other submission/lifecycle cases in the compatibility batch pass. This is an explicitly reported baseline fixture limitation, not a full-system pass claim.

## Production verification and remaining boundaries

Pre-release read-only inventory confirms both platform administrators, all existing overrides and the five Simulator mappings are unchanged; the authorization marker table does not yet exist on the Phase 4 deployment. Final release verification must confirm exact Render SHA, successful additive bootstrap/worker start, health/login, security headers, published assets, authorized read-only operational/administration pages, and the activation receipt/mappings. No synthetic production missions, employee changes, fueling/inventory writes, account conversions or fabricated vendor affiliations are permitted.

No production permission migration is claimed until the protected cutover receipt and all fourteen/five mappings are verified. Future non-platform node Grandmaster ambiguity, changed inventory, unavailable MFA step-up and rollback after later legitimate edits remain **stop-and-review** conditions. No owner must supply a password in chat. No Phase 6, NeoBid cleanup or FOB → UPLIFT work starts automatically.
