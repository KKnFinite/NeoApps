# Accounts & Access Phase 2 — inactive foundation and production inventory

**Historical pre-release snapshot:** the inventory and HOLD statements below record the original `e866c2d6` review. The owner subsequently commissioned the verified backup through the protected interface. Current integration, administrator and release evidence is in `2026-11-17-accounts-access-phase2-completion.md`; no new authorization model is activated.

Date: 2026-11-17. Starting source and verified existing LIVE SHA:
`9a67645aff530613f4b9c02f05382b78c02e83c7`.

**Local implementation verified. Release held: owner and backup-administrator identities have not been verified through authenticated NeoApps evidence. No push, deployment, production DDL, grant migration or account conversion is authorized by this report. The existing Phase 1 deployment remains LIVE.**

## Production inventory and release gates

The Neon connector failed, and the existing CLI identity could not access the organization. After the user signed into the Neon console, read-only SQL succeeded on the NeoGateway project `proud-wind-91564002`, production branch `br-aged-fire-aj4msg03`, database `neondb`. The actual NeoApps user, application and node-grant tables were verified before inventory. Queries used explicit READ ONLY transactions ending in ROLLBACK. Aggregate results were collected around 18:24–18:38 Central. These are point-in-time database observations, not inferred seed defaults. No names, addresses, UPS IDs, credentials or per-account mappings are included in this report.

The accompanying `2026-11-17-accounts-access-phase2-inventory.sql` makes the aggregate checks repeatable under a single repeatable-read snapshot. No application startup or lazy grant-creation getters were run against production.

| Inventory | Observed result |
|---|---|
| Users | 57 total, all active; one stored and effective non-vendor platform Grandmaster, 56 Watchers, zero platform Masters |
| NeoGateway entitlements | 53 approved (47 Watcher, two Operator, three Simulator, one Grandmaster); three pending |
| NeoStaffing entitlements | 21 approved (one Watcher, 12 Operator, one Simulator, six Master, one Grandmaster); 16 pending, two denied |
| NeoBid | Seven pending requests, zero approved grants; untouched |
| Gateway memberships | 53 approved, three pending, all active, at the existing gateway |
| Stored node grants | 366 active explicit assignments; seven Grandmaster grants, one per existing node |
| Effective node administrators | 17 Master assignments and seven Grandmaster assignments; these are scope assignments, not 24 distinct people |
| App-administration conjunction | Zero platform-Master + same-app-Grandmaster combinations; the existing platform Grandmaster has protected oversight |
| Restricted vendor accounts | One; its UPS ID is missing. No correction or conversion was attempted |
| Stored permission thresholds | 159 rows; 34 observed differences from the 154 current source defaults, plus five non-default keys |

Effective node Master counts: MotherBrain two, Sektor two, Ermac two, Scorpion four, SubZero four, Rain two, Reptile one. Each also has one Grandmaster assignment. Application Masters retain their existing operational rights; none was promoted to platform Master.

Six persisted platform administration thresholds remain Master (`neoapps.access_requests.{view,edit}`, `neoapps.portal_management.{view,edit}`, `neoapps.user_management.{view,edit}`). Phase 1's protected runtime Grandmaster floor still overrides those stored thresholds. They were inventoried, not rewritten. Other observed overrides concern parking, planning, display and operational thresholds; this phase does not reinterpret them as grant authority. The five additional keys are `neoermac.door_view.enter_actual_pulls`, `neosektor.driver_routing.edit`, `neosektor.tunnel_conductor.view`, `neostaffing.attendance.edit`, and `neostaffing.org_chart.edit`.

### Read-only current versus explicit-node comparison

Across 371 eligible user/gateway/node pairs, the current adapter resolves a role for all 371; 366 have explicit node grants. Exactly five pairs would lose inherited Simulator access under an immediate explicit-only cutover: one each in MotherBrain, Sektor, Ermac, Rain and Reptile. This is a concrete mapping prerequisite, not permission to create those grants. The other 366 role projections match. This comparison excludes inactive/revoked gates and restricted vendors and respects existing membership/application gates. It compares role-source inputs, not every action-level permission or all future administrative policy decisions.

An early diagnostic used PostgreSQL's reserved `CURRENT_ROLE` spelling as an unqualified output alias and produced an invalid comparison. That result was discarded; the corrected `effective_rank` query above produced the five-gap result. No application behavior was inferred from the discarded query.

### Unresolved authenticated identity evidence

The signed-in Neon account proves database access, not ownership of a particular NeoApps identity. A Grandmaster role likewise does not establish owner designation. No authenticated owner-to-NeoApps-account binding or designated backup identity has been established. The user was asked for verified account identifiers; none is assumed. With one effective platform Grandmaster, redundancy/recovery is a material unresolved gate. Do not create a backup, commission a peer Grandmaster, reset credentials or deploy an authority change to solve this implicitly.

## Implemented architecture

`app/services/account_authority.py` provides immutable typed scopes: platform `neoapps`, application `app:<code>`, gateway `gateway:<id>`, and node `node:<gateway-id>:<node-id>`. It reuses the existing five role names and levels. Scope validation prevents mixed application/gateway/node identities. Exact node scope and existing app/membership/resource gates are required. No app role automatically becomes a gateway administrator rank. Parent platform oversight is a distinct administrative capability, not a fictional inherited operational role.

The adapter reads bounded projections without read-side seeding or global caches. Its current side preserves documented legacy fallback for comparison. Its proposed side reads only explicit staged assignments. `compare_authority` returns differences for review, never authorizes by combining old and new rights. Whole-population comparisons must use a consistent read-only snapshot. The current live `permission_rules.user_can`, routes, login, grant getters and operational authorization remain unchanged.

Application account administration requires platform Master **and** Grandmaster in that same approved application, or explicit protected platform-Grandmaster oversight. Node Master delegation is confined to that node and below Master. Both persisted existing rank (including inactive higher-rank tombstones) and proposed rank are checked. Self changes, unrelated-scope authority, ordinary Grandmaster commissioning and ordinary platform-Grandmaster removal fail closed. New nodes receive no staged grant automatically. Existing node Grandmasters are not retired or converted.

### Additive persistence

Three empty-by-default tables are registered with the existing bootstrap:

* `account_scoped_roles`: candidate assignments only, unique user/scope, typed scope/FKs, role checks, positive optimistic version and immutable audit receipt reference. It is **not a live grant source**. There is no runtime authorization cutover switch.
* `account_access_requests`: individual attempts, immutable retry identity, reason, state, positive version. A partial unique index permits one pending attempt per user/app in SQLite and PostgreSQL; a separate unique user/retry-key constraint handles duplicate delivery.
* `account_access_decisions`: one immutable approval/denial/withdrawal per attempt, reason and reference to the shared Phase 1 security ledger. A denied attempt stays closed; a later request creates a new attempt.

`PortalAppAccess` remains the current entitlement store. It receives no new fields or backfill. Legacy pending requests are not guessed into historical attempts or duplicated; the future compatibility bridge must review them. An approval recorded in the new foundation is explicitly **unfulfilled**, not effective access. `fulfill_access_request` always rejects until an explicitly reviewed later implementation. No existing UI or HTTP endpoint calls the new request or staging services yet. Registration and existing administration keep their Phase 1 behavior. This phase does not claim that legacy request history has been reconstructed or that the future request UI is active.

### Transactions, history and sessions

`app/services/account_foundation.py` reuses Phase 1's existing administration serialization anchor and fresh actor checks. It locks actor/target/request or candidate-role state, validates actor and target session versions, scope, expected row version and current entitlement fingerprint. The fingerprint covers every existing entitlement field, so older writers need not know about a new version column. Updates use compare-and-set as well as row locking. Duplicate decisions and stale role changes fail without another receipt.

Successful calls leave commit to the request owner. Any failure rolls back the entire account-action transaction, including a flushed receipt. Requests, decisions and staged changes record actor, subject, time, reason and before/after facts in the shared `AccountSecurityEvent` ledger. No password, MFA secret or token is recorded. Request-local caches are cleared on service exit and existing commit/rollback hooks remain in use. Because these changes do not alter effective access, they do not rotate or invalidate legitimate live sessions. Future effective fulfillment/revocation must perform the existing session-version invalidation at cutover; it cannot invoke the disabled fulfillment stub as if access had changed.

`account_foundation_schema.py` installs idempotent SQLite/PostgreSQL UPDATE/DELETE guards on the shared security ledger and new decision table, plus PostgreSQL TRUNCATE guards. These protect history against bulk SQL as well as ORM changes. The bootstrap hook adds only tables/constraints/guards; it does not change existing account fields, credentials, grants or history. Database-owner DDL remains trusted; these guards are not a claim of tamper-proof storage against a database owner.

## Validation

All mutations used in-memory SQLite or isolated schemas in the existing loopback-only synthetic PostgreSQL database. Existing synthetic directories/schemas were retained. No production account, grant, vendor, employee, fueling, inventory, Sort or external-provider mutation occurred.

| Check | Result |
|---|---|
| Main compatibility/security run (Phase 1, foundation, bootstrap, auth, revocation, Smart Login, CSRF, headers, vendor/nightly lifecycle) | 252 passed; 622 subtests; zero failed/skipped |
| Foundation + canonical access-control tests | 40 passed; 56 subtests; zero failed/skipped; overlaps 16 cases / 41 subtests above |
| Final foundation after inactive-target and malformed-scope checks | 18 passed; 42 subtests; zero failed/skipped |
| Combined unique SQLite/Python coverage | **276 cases, 638 subtests passed**; not the sum of repeated runs |
| PostgreSQL foundation + Phase 1 recovery, revocation, last-admin and vendor/Sort races | **33 passed; eight subtests; zero failed/skipped** |
| Final focused PostgreSQL foundation repeat | Eight passed; six subtests; zero failed/skipped; included in the 33 unique cases |
| Compilation / diff validation | Eight affected Python files compiled in memory; `git diff --check` passed |

Coverage includes exact scopes, application-admin conjunction, no cross-scope privilege maximum, inactive higher-target protection, self/peer/last-platform-admin safeguards, denied reapplication, one-open-request uniqueness, idempotent request delivery, approve/deny races, stale entitlement/session/version rejection, actor revocation after a lock wait, candidate-role races, injected decision-insert rollback, immutable bulk history, idempotent DDL, mapped synthetic decision equivalence and unchanged live access. Five real production fallback gaps remain unconverted.

Evidence logs: local ignored `instance/phase2-security-tests.log`, `instance/phase2-authority-final.log`, `instance/phase2-postgres-tests.log`; subsequent focused checks/receipt are recorded locally alongside them. Existing deprecation warnings were visible and not suppressed. No tests were deleted, skipped broadly or weakened. No JavaScript/templates/assets changed, so no UI/browser regression sweep was run; browser use was limited to the authenticated read-only production inventory. The broad 367-ID audit was not run.

## Exact changed files

* `app/models/account_foundation.py`
* `app/models/__init__.py`
* `app/services/account_authority.py`
* `app/services/account_foundation.py`
* `app/services/account_foundation_schema.py`
* `app/services/database_bootstrap.py`
* `tests/test_account_foundation.py`
* `tests/test_account_foundation_postgres.py`
* `docs/audits/2026-11-17-accounts-access-phase2-inventory.sql`
* This report.

The user-supplied redesign audit remains untracked and outside this change. All unrelated artwork, reports, prototypes, jobs and unfinished work were preserved.

## Release status and Phase 3 prerequisites

No push/AutoDeploy is performed while authenticated owner/backup verification is insufficient. The local review commit containing this report is preserved on `codex/accounts-access-phase2-foundation`; its exact SHA is included in the delivery and local ignored `instance/phase2-review-receipt.json`. Render's latest deployment was rechecked as **LIVE**, SHA `9a67645aff530613f4b9c02f05382b78c02e83c7`, deployment `dep-dbueft61egvs73amu2cg`. That is Phase 1, not this local foundation. Do not label this patch LIVE or claim production schema compatibility has been exercised by deploying it. Local tests cover bootstrap compatibility. Read-only GETs of `/healthz` and `/login` both returned 200 with HSTS/CSP, and the login form contained a CSRF field. No new rollout/startup verification is claimed because no Phase 2 deployment occurred.

Before release/cutover review: verify the owner's NeoApps identity and backup designation through authenticated evidence; review a restricted per-account mapping and the five inherited Simulator pairs; decide gateway-wide administrator resource bounds and protected peer commissioning/recovery; approve legacy-request import/fulfillment behavior and audit retention/access. A new authorization model must not activate until all mapping differences are explained and signed off. Keep current writer/reader compatibility throughout any later cutover; never union staged and legacy rights or restore revoked grants through fallback.

**Stop after Phase 2.** No Accounts & Access UI, registration redesign, preferred-app/CryoCheck work, NeoBid cleanup, driver/supervisor conversion, node-Grandmaster retirement or Phase 3 implementation was started.
