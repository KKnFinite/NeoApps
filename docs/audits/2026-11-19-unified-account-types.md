# Unified NeoApps account types and completed release

## Scope and release authorization

Source: latest fetched `origin/main` at `9b22eb0cbe31254ec1758283e1eab8309a38e5a7`. Completed local ancestors are registration-ID validation `5c7377c19d1f661dc8a89aecf457009f69072cbc` and account deletion/shared People search `b437b29561c9ba2eaa5b73d5a53a5c1c2d58f5e6`.

The owner explicitly removed the active-Sort deployment hold and authorized one release including these completed changes. This supersedes the deployment-hold sections of the November 18 registration and deletion reports. No operational scheduling or deployment-hold setting is changed in application code.

## Implemented contract

* Shared signup offers Full NeoApps, Fuel Truck Driver and Vendor Supervisor. Full applicants request only available catalog applications; NeoBid remains unavailable. Restricted requests use NeoGateway only and do not create an approved restricted account. All new registration IDs must be seven or ten ASCII digits.
* Access & Roles has one account-type/approval form. Platform administrators can approve, change the requested type, choose available Full application roles, deny, or convert the same identity. Supplied reasons are retained; decision reasons remain optional. Existing per-application delegated authority remains separately enforced by the shared authorization engine.
* Original signup type is an immutable `account_type_requested` receipt. Final type, outcome, selected applications and actor are recorded in `account_type_decided`. Existing request/decision history is retained. Signed account-state reviews, Sort-first locks, fresh actor checks, session invalidation and atomic rollback protect mutations.
* Restricted conversions revoke incompatible application/node/gateway authority without restoring old roles on a later conversion. Platform Grandmasters and self-conversion remain protected. Canonical Staffing-person identity checks and normalized duplicate-ID checks remain authoritative. A connected driver or pending truck claim blocks conversion to Supervisor until Dispatch releases it.
* Drivers retain pending truck requests and nightly Dispatcher approval. Supervisor login goes directly to a read-only, gateway-scoped operations view, including a valid NO ACTIVE SORT state. Supervisor accounts cannot claim trucks, perform driver/fuel actions, manage inventory or enter ordinary applications. Company parameters cannot alter server scope.
* Company selection, verification and company-owned authorization are retired from the UI and HTTP mutations. Old company POSTs return 410 after existing authentication/CSRF guards; the legacy company GET redirects to Accounts security. Historical companies/memberships remain stored. Previously administrator-assigned Supervisor capabilities remain readable through a nullable compatibility bridge, until an explicit account-type decision replaces it; no affiliation or authority is fabricated or backfilled.
* People supports separate Driver/Supervisor filters and labels. The persistent People search and protected account-deletion controls remain intact. Original request and final account-type decisions are visible in History.

## Database compatibility

One nullable `fuel_driver_identities.account_type VARCHAR(24)` column is added by existing SQLite/PostgreSQL idempotent schema synchronization. It records the current requested/approved classification; it grants nothing without the existing approved restricted-account boundary. NULL preserves legacy interpretation. No existing users, roles, IDs, company associations or operational evidence are converted at startup. Old code ignores the additional column, allowing application rollback. Frozen FROST models, operational evidence and all unrelated data are unaffected.

## Executed validation

**91 unique SQLite/Python cases passed across focused runs**, comprising registration ID validation (6), Accounts workspace (14), account deletion (13), Phase 1 security (14), Fuel Driver accounts (11), nightly driver/Supervisor access (5), unified account types (8, including active scoped-engine conversion), and existing scoped authorization (20). This counts unique cases, not repeated runs or passing subtests. Initial setup/contract failures were corrected and rerun; no unresolved failures in this set. No broad system audit was run.

**16 PostgreSQL cases passed** in isolated synthetic schemas: unified competing decisions, revoked administrator, final-audit rollback and additive-column upgrade (4); existing driver identity/company-history serialization and conversion guards (6); pending claim/approval rollback and exactly-once connection (2); protected deletion concurrency, rollback and immutable receipt (4). Existing synthetic schemas and directories remain preserved.

**Two browser cases / six subtests passed.** Chromium and WebKit each exercised desktop 1280px and mobile 390px registration as Supervisor, administrator approval as Driver, pending truck claim, Dispatcher approval, live update, actual assignment visibility, company-free Supervisor approval, read-only mission display, and revocation/session invalidation. The deletion/search workflow additionally exercised desktop Chromium and mobile WebKit, confirmation cancellation, synthetic deletion and persistent People search. All account actions use actual local HTTP/CSRF flows; external providers are mocked. Screenshots: `instance/browser-evidence/accounts-phase4/` and `account-deletion-search/`. WebKit executes layout/actions without its CSP-conflicting optional screenshot helper.

One existing JavaScript vendor-refresh regression passed; changed `accounts.js` passes Node syntax checking. Changed Python sources parse, UTF-8 templates render, and `git diff --check` passes. Existing deprecation warnings remain reported and are not suppressed.

Evidence logs under `instance/`: `unified-focused.log` (first run and independently diagnosed failures), `unified-focused2.log` (42 passing), `unified-scoped-live.log` (1 passing), `unified-sort-final.log` and `unified-gateway-final.log` (final driver/gateway checks), `unified-postgres.log` (16 passing), `unified-browser.log` (2 passing). The initial 69-case run plus corrected subset, scoped tests and added active-driver guard establish the 91 unique-case total.

## Production verification and limits

Before push, Render's existing release was LIVE, and an authenticated read-only Accounts security page confirmed scoped authorization remains active. No production registrations, approvals, deletions, conversions, claims, inventory changes or synthetic operational actions were performed. No production company cleanup or scope cutover is part of this release. Unrelated untracked files and existing jobs are preserved.

After the single push, verify exact Render commit/deployment status, startup/bootstrap logs, HTTP health/login, published script hash, and authenticated Accounts/operational reads. Exact LIVE identifiers and rollout observations are reported in the completion message after AutoDeploy; tests alone do not establish production deployment.

Supervisor visibility intentionally covers the account's gateway rather than a vendor company, as requested. Existing disabled accounts remain disabled. Converting an active driver can require Dispatcher release first; the feature does not discard physical work to force a conversion.
