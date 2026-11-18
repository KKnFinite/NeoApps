# Phase 2 integration and release evidence

Commissioning feature LIVE: `7b7571d153bdff4c2bb50d8c2e227f9863302896`, Render `dep-dbug8hou01pc73euv9r0`. Preserved foundation candidate: `e866c2d61b643b1c4ccbc34ebe9bcf9767e4d133`.

**Owner commissioning verified; integrated foundation cleared for release.** Exact deployment SHA/status and subsequent checks are recorded in the release receipt and final delivery. This report does not treat a push or queued deployment as LIVE.

The commissioning feature was released independently from Phase 1. Bootstrap completed, the existing password was preserved, the worker booted, health/login returned HTTP 200 with HSTS/CSP, and an authenticated GET as Kessler displayed the protected User 36 form. Database UPDATE/DELETE/TRUNCATE history guards are installed. The original seven node-role audit records retained their exact pre-release digest. Codex did not submit a production promotion.

## Integration architecture and compatibility

The preserved Phase 2 branch is integrated locally with the commissioning release. The sole source conflict was the bootstrap history-guard hook. It was resolved by extending the deployed `account_security_history.ensure_account_security_history` helper to both history tables through the thin `account_foundation_schema` wrapper. There is one append-only guard implementation, with the same function/trigger identities and no duplicated permission evaluator or automatic grant mapping.

The additive foundation remains exactly scoped to the earlier design: typed candidate-role records, individual access attempts, immutable decisions linked to the shared security ledger, expected-version/session/entitlement conflict checks and transaction rollback. Existing `User.role`, application entitlements, gateway memberships, node grants, login and live authorization remain their current sources of truth. The proposed scope adapter is shadow-only, request fulfillment is disabled, and no existing route is switched to staged roles. No read-side provisioning is introduced.

The new tables are empty by default. No account conversion, historical-request reconstruction, role backfill, NeoBid cleanup or authority migration is performed. Current NeoNode Grandmaster roles remain unchanged. Commissioning is the separately authorized owner action, not a permission migration.

## Focused verification of the combined source

| Check | Actual result |
|---|---|
| Combined Python: foundation, commissioning, Phase 1, bootstrap, revocation, canonical access and Smart Login | **120 passed; 138 subtests; zero failed/skipped** |
| Combined isolated PostgreSQL: foundation, commissioning and Phase 1 security transactions | **22 passed; 11 subtests; zero failed/skipped** |
| Prior commissioning release checks | 198 Python cases / 316 subtests; 31 PostgreSQL cases / five subtests; one desktop/mobile Chromium workflow passed |
| Confirmation-label clarification after the owner's mobile feedback | 13 commissioning cases / 11 subtests passed; no authorization behavior changed |

These are separate, overlapping runs, not additive unique-test totals. Existing warnings were not suppressed. No tests were deleted or weakened. The broad audit inventory was not run.

Evidence is retained locally under `instance/phase2-integrated-python.log`, `instance/phase2-integrated-postgres.log`, the commissioning release logs and browser screenshots. Synthetic schemas/databases and unrelated work were preserved. No synthetic production test action was performed.

## Owner-action and production release gate

The verified primary is User 1 and the backup is User 36. The owner executed `/admin/users/36/commission-grandmaster`, using the current-password, exact-ID, reason and explicit platform-authority controls. Codex did not submit the action or enter the owner's password. Signed-out login follows the existing normal landing behavior; reopening the protected URL in the same signed-in browser reaches the form. Mobile use does not require the desktop. The owner's feedback exposed ambiguity between an employee ID and a NeoApps account number; the label now explicitly says “Type 36 to confirm Brad Leach” for this target, with an equally explicit server rejection message. The ID is not prefilled and explicit confirmation remains required.

Read-only production confirmation succeeded after the owner reported completion:

* Both IDs have actual platform role Grandmaster, active/verified/unrestricted status and supported recoverability; primary session version remains 1. No MFA or forced-password-change flags were altered.
* Exactly one `platform_grandmaster_commissioned` event, **audit ID 8 at 2026-11-18 02:11:26.361 UTC**, identifies actor 1 and subject 36, explicit `neoapps` scope, current-password step-up and both confirmations. Its before state is Watcher/session 2 and after state is Grandmaster/session 3. Its before/after scoped-grant fingerprints match. Brad's earlier sessions therefore require a fresh sign-in through the existing version checks.
* Brad's existing application/node grants are preserved. His NeoStaffing and seven NeoNode Grandmaster grants exceed his earlier baseline and are not required for platform-backup designation; operational necessity remains an owner review, with no automatic changes.
* All five User 3 inherited Simulator pairs remain on gateway 1 / membership 3, source app-access 7, nodes 1/2/4/5/7. Proposed explicit mappings retain Simulator and are not materialized.

Independent app/node database digests for both administrators match their pre-commissioning snapshots. All seven historical audit records retain digest `064dbcc434bf5709a4f6bdb7e439ba8b`. The authorized new platform appointment is the sole account-role change in this work; no application/node grant was rewritten or silently granted.

Those checks satisfy the administrator release gate. The reviewed merge is committed, fast-forwarded to main and pushed only after successful local validation. Render AutoDeploy must reach that exact SHA LIVE, with successful bootstrap/worker startup, public health/security checks, unchanged administrator/grant evidence, and empty additive foundation tables. No new authorization model is activated. Actual post-deploy evidence is recorded in the ignored `instance/phase2-final-live-receipt.json` and final delivery, without an extra documentation-only deployment.

Phase 3 remains out of scope. No Issues, temporary workflows, production cleanup, operational writes, permission revocation or unrelated changes are included.
