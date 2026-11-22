# Database identity cutover rehearsal

This candidate changes release tooling only. The existing application and Gunicorn entry points continue using `DATABASE_URL`. The commands below document an owner-controlled release. Execution against production requires separate approval.

## Roles and custody

| Login | Required access | Custody |
| --- | --- | --- |
| Web and in-process workers | Connect to the application database, use the application schema, explicit relation privileges in `scripts/runtime_database_grants.json`, single-column update rights in `scripts/runtime_lock_columns.json`, usage on sequences belonging to insertable tables, read the deployment receipt | The one runtime DSN on the existing Render service |
| Prior compatible runtime | Independently generated credential and independently verified restricted grants for fallback | Owner's existing secret store until rollback |
| Migration | Connect, usage and create in the application schema, ownership of inventoried application objects and newly created objects, canonical bootstrap/seed access | Existing operator workstation under a separate OS account, outside Render |
| DBA | Provision identities, review ownership, network access, existing ACLs and recovery | Existing DBA custody |

Create ordinary SQL login roles with SCRAM secrets. Use NOSUPERUSER, NOCREATEROLE, NOCREATEDB, NOREPLICATION and NOBYPASSRLS. The runtime identities have no role memberships, object ownership or grant options. Supply secrets through the existing protected credential store and process environment. Use provider-supported TLS with server identity verification. Check the effective role on the actual connection. [Neon role guide](https://neon.com/docs/manage/roles)

A separate Render pre-deploy instance does not provide an independent place to store a migration password for this service. Environment variables and secret files belong to the service. The migration checkout, its OS account, credential store, release tokens and private keys must be inaccessible to the web service. Remove old owner credentials from service variables, linked groups, files and build configuration. Keep the off-service helper out of client-controlled environments. [Render deployments](https://render.com/docs/deploys) · [Render service secrets](https://render.com/docs/configure-environment-variables)

## What the policy checks

The manifest lists table verbs used by application services. Evidence includes isolated SQL traces, the employee recovery service's explicit owned-table list, PostgreSQL history triggers and concurrent application tests. Permission does not establish an employee's entitlement. The existing application authorization remains responsible for user and gateway isolation. Two example append-only relations are account decisions and account security events.

Row locks on PostgreSQL can require update permission even for read-only selections. The column file specifies one writable column on relations where the service needs a row lock and has no general update grant. The platform permission conversion also locks its existing inventory in SHARE ROW EXCLUSIVE mode. Its node-catalog lock requires a table-level right. UPDATE supports that lock and the existing catalog maintenance operation. [PostgreSQL lock privileges](https://www.postgresql.org/docs/17/sql-lock.html)

The gate checks effective rights including PUBLIC, column ACLs, grant options, sequences, inherited memberships, object ownership, executable security-definer routines, accessible relations outside the application schema and relevant default ACLs. Objects outside the manifest receive no automatic grants. Extra rights cause a transaction failure. Review new objects and future migrations explicitly. The catalog verification runs once during release.

## Before the first production switch

1. Owner and DBA identify the exact production database, schema, release SHA, migration host, operators and credential stores. Compare production-only objects, routines, custom types, extensions, foreign access, RLS policies, connectable databases, default privileges and shared-schema consumers to the rehearsed catalog. Keep object lists private. An unclassified object or a required permission absent from the policy blocks the switch.
2. Use the existing restricted DBA channel to create the migration login and two runtime logins. Apply CONNECT to the target database and USAGE on the application schema. Give the migration login CREATE in that schema. Review PUBLIC database TEMP, schema CREATE and all shared grants first. Any proposed change affecting another principal requires its own reviewed impact assessment. Do not blanket-revoke shared privileges.
3. Obtain a clean checkout of the exact release on the owner machine. Record the current deployment, release commands, accessible rollback build, credential versions, durable worker receipts and applicable business/audit fingerprints. Store references without credential values. Pause concurrent schema changes and reserve the first release so AutoDeploy cannot publish it early.
4. Using the authorized transfer identity on the owner machine, generate a private catalog inventory. The file records object IDs, names, routine signatures, database and roles. The DBA compares **every entry** with the application inventory. Abort if extensions, custom types, another application or unrelated objects need special handling. The transfer changes only reviewed relations, column-owned sequences and routines. It never uses a database-wide ownership reassignment.

   ```text
   python scripts/review_database_ownership.py --database DB --schema SCHEMA --previous PREVIOUS_OWNER --target MIGRATOR --inventory PRIVATE_PLAN
   python scripts/review_database_ownership.py --database DB --schema SCHEMA --previous PREVIOUS_OWNER --target MIGRATOR --apply-reviewed PRIVATE_PLAN
   ```

   PostgreSQL requires the transfer operator to be entitled to transfer each object to the target. Only the DBA performs this step. Existing web and worker traffic must be drained or have its required explicit runtime grants staged before its old ownership is removed. For the first switch, use a brief controlled drain with an operator available. Preserve the old login under private custody until the fallback passes.
5. Run the canonical migration off-service with the dedicated migration DSN and its bootstrap secret. Use a shell or release job that aborts on **each** nonzero exit. Verify the release SHA matches the clean checkout. Stop on schema repair proposals, changed historical rows or unexpected bootstrap effects. Seal each restricted identity's policy after reviewing the migration result.

   ```text
   python scripts/bootstrap_database.py
   python scripts/runtime_database_access.py --database DB --schema SCHEMA --runtime WEB --release FULL_SHA --apply
   python scripts/runtime_database_access.py --database DB --schema SCHEMA --runtime FALLBACK --release FULL_SHA --apply
   ```

   The second step creates a small owner-controlled deployment receipt table on the first approved run. Grants and the receipt commit together. Test both logins in fresh processes using the next command with the matching login name and release. Keep the fallback credential off Render until needed.

6. In the same controlled release, set the existing Render service's DATABASE_URL to the restricted runtime login and replace its pre-deploy command with the following. Set DB, SCHEMA and WEB to actual nonsecret identifiers in the command. Render supplies RENDER_GIT_COMMIT. Keep the existing build, start, worker and Sort-hour settings.

   ```text
   python scripts/runtime_database_access.py --database DB --schema SCHEMA --runtime WEB
   ```

   A wrong effective login, missing release receipt or privilege drift fails the deployment. Future releases run the canonical migration and stamp the exact commit off-service before deploying it. Select the exact commit when releasing; coordinate concurrent main updates.

7. Confirm Render's live revision, health and deployment logs. From each new web/worker process confirm the runtime identity, credential-source inventory and absence of access to the operator's vault, deployment token or owner DSN. Verify old pools have drained, worker receipts still progress at their normal deadlines, and authorized/unauthorized application access still behaves correctly. Use existing read-only evidence and natural events. Compare fingerprints over a quiescent interval or account for concurrent legitimate writes. Sign off before retiring or rotating the former broad credential. Never leave it accessible from the running service.

## Rollback

A failed pre-deploy check, unexpected permission denial, missed worker receipt, authorization mismatch or unexplained change to business records stops the release. Retain logs and keep traffic drained if necessary. For a credential or connection failure, replace the runtime DSN with the separately validated fallback, change the verification command's expected runtime name to match, and restart all web workers so pools reconnect. Check the receipt for the exact compatible build, effective roles, read-only health, audit fingerprints and worker progress. This preserves the additive schema and history.

The fallback has the same reviewed operational policy. A missing grant affecting both identities requires a specifically reviewed correction or a previously tested compatible policy and build. Returning an elevated migration DSN to the web service is outside this procedure. Do not remove a receipt, reverse a migration, discard queued work or reuse an old Render release configuration containing an elevated credential to clear a failed gate.

## Reproduce locally

Use installed PostgreSQL binaries and the repository's Python dependencies. The command starts a short-lived SCRAM server bound to loopback and uses generated credentials, synthetic data and existing integration doubles. It ignores external DATABASE_URL values. The upstream test setup creates fixtures, then normal application calls and concurrent workers run on a fresh restricted SQLAlchemy pool. A listener rejects SQL from any other application login during the operational assertions.

```text
python tests/database_roles/run_local.py --bin PATH_TO_LOCAL_POSTGRES_BIN
```

The release rehearsal uses a separate database. It bootstraps with a prior owner, fingerprints all business relations, transfers reviewed ownership, repeats the canonical bootstrap with the migration login, tests the read-only gate, drains connections and reconnects with the fallback. Actual hosted isolation, provider-specific catalog differences and production-only paths require owner/DBA verification. This preparation creates no cloud resource or paid service.
