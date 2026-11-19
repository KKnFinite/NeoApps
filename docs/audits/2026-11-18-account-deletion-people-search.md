# Account deletion and persistent People search

Starting local main: `5c7377c19d1f661dc8a89aecf457009f69072cbc`, the verified registration-ID change awaiting a safe deployment window. Fetched origin/main remains `9b22eb0cbe31254ec1758283e1eab8309a38e5a7`. No unrelated files were staged or discarded.

## Implemented

- Security-tab red **DELETE ACCOUNT** control, restricted to active, verified platform Grandmasters and the existing scoped platform-admin check. It requires a current signed review, CSRF, the exact target NeoApps account number, a confirmation checkbox and the existing browser confirmation. Direct requests enforce the same checks.
- Self-deletion and protected platform Grandmasters reject. Existing last-administrator protection remains. Node/application authority does not grant deletion authority.
- Deletion removes the account from People/current Requests, revokes sessions, invalidates recovery/verification links and the login credential, disables application/gateway/node/scoped grants and vendor access, ends current vendor capability, and withdraws pending access requests with immutable decisions.
- The User identity row and all operational, Staffing, historical assignment and audit references remain intact. An immutable `account_deleted` security receipt is the permanent deletion marker; no schema change, row cascade or historical rewrite is needed. Historical profiles are read-only and clearly marked Deleted. Existing identities/IDs remain reserved rather than being reassigned to a new account.
- Current Scorpion nightly assets, unfinished assignments, truck claims and pending operational requests block deletion with an explicit explanation. Existing Sort/resource lock order is reused before account locks; permissions and Sort identity are rechecked after waits. Duplicate/stale deletion and reactivation through the shared legacy editor reject.
- One People search form now lives immediately beneath the shared People / Requests / Security & Permissions navigation, including profile and conflict pages. It opens the existing People results, searches name/email/Employee ID, and preserves People filters. The People filter form retains the search term without a duplicate visible search input. Existing request-specific filtering remains separate. Unauthorized users do not gain People search access.

## Focused verification

Across focused runs, **52 unique Python cases passed**: 13 deletion/search cases, 14 Accounts & Access cases, 11 Fuel Truck Driver account cases and 14 Phase 1 security cases. These are targeted modules, not a full-system result.

**Four PostgreSQL cases passed** on a new isolated schema in the existing loopback synthetic database: concurrent duplicate deletion commits once; waiting deletion rejects a revoked administrator; failure after audit insertion rolls back all changes; direct SQL UPDATE/DELETE of the deletion receipt is rejected by the existing immutable-history trigger.

**One browser case with two passing subtests** exercised desktop Chromium and mobile WebKit under enforced CSP/CSRF: persistent search position/navigation, no duplicate People search, confirmation cancellation without a POST, actual synthetic deletion, hidden People row, retained identity/audit, and no horizontal overflow. Chromium additionally rendered the mobile viewport for screenshot inspection. WebKit layout/action assertions ran; its optional screenshot call is omitted by the existing CSP-safe test helper.

JavaScript behavior reuses the existing declarative confirmation handler. No new script or stylesheet is introduced. Python source parsing and staged diff checks pass. Existing deprecation warnings remain visible; no skips or expected-failure markers were added.

Local evidence: `instance/account-deletion-final-python.log`, `account-deletion-regression.log`, `account-deletion-security.log`, `account-deletion-scoped.log`, `account-deletion-final-postgres.log`, `account-deletion-final-browser.log`; screenshots under `instance/browser-evidence/account-deletion-search/`.

Application files: `app/services/account_deletion.py`, `app/services/accounts_workspace.py`, `app/auth/accounts.py`, `app/auth/routes.py`, and the shared navigation, People and profile templates under `app/templates/auth/accounts/`. New tests are `tests/test_account_deletion.py`, `tests/test_account_deletion_postgres.py`, and `tests/browser/test_account_deletion_search.py`.

## Deployment status

**Local verified release; push/AutoDeploy held.** Read-only production settings still enable RFD Wednesday/Thursday night Sorts, 21:00–05:00 Central. The verification occurred inside the Wednesday night window. The owner's outside-active-Sort restriction remains in force. Release requires a fresh operational check after 05:00 Central, safe integration of any intervening main changes, and verification of the resulting exact Render commit.

Unchanged production is LIVE at `9b22eb0cbe31254ec1758283e1eab8309a38e5a7`, deployment `dep-dbv5si7f3r2c73fghff0`. Read-only `/healthz` and `/login` returned HTTP 200 with CSP/HSTS. These checks verify the existing release, **not deployment of this feature**. No production deletion, registration, account conversion or operational test mutation occurred. No GitHub Issues, temporary workflows, CI runs or job cleanup were performed.
