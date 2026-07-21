# NeoGateway / NeoApps

NeoGateway is the public platform brand. NeoApps is the technical Flask framework powering the operational tools. RFD is the current default Rockford Air Hub gateway workspace, and NeoMotherBrain is the operations core module inside that workspace.

## Access Model

NeoApps authentication is global. RFD is the current default gateway, so RFD is selected automatically and no gateway selector is shown yet.

GatewayMembership grants access to a gateway only. Approved gateway members have default `watcher` access to active NeoNodes in that gateway. GatewayNodeRole stores elevated node-specific roles using this ladder:

```text
watcher < operator < simulator < master < grandmaster
```

Operational data stays in shared tables and is scoped by the current gateway.

New public account requests create a global NeoApps user and a pending RFD GatewayMembership. Email verification is required before a Master or Grandmaster can approve access. Approved gateway members receive default `watcher` access to active NeoNodes unless a GatewayNodeRole grants a higher node-specific role.

## Email Configuration

Transactional email uses Brevo and reads configuration only from environment variables:

```text
BREVO_API_KEY
MAIL_FROM_NAME
MAIL_FROM_EMAIL
APP_BASE_URL
```

Local development and tests safely no-op email sending when required mail configuration is missing.

## Database Configuration

Local development uses SQLite when `DATABASE_URL` is not set. Render/production should set `DATABASE_URL` to the Neon Postgres connection string.

Do not commit `DATABASE_URL` or database credentials.

## Authentication Abuse Protection

Login and forgot-password requests use the shared database table
`auth_rate_limit_states`, so limits apply across all production workers. The
normal schema sync/bootstrap workflow creates the table, including during
`scripts\bootstrap_database.py` deployments.

Defaults: login allows 10 failures per IP and 5 per account identifier in 15
minutes; forgot-password allows 5 requests per IP and 3 per email in one hour.
Both use escalating temporary cooldowns, never permanent account lockouts.

Rate limiting is enabled by default with `AUTH_RATE_LIMIT_STORAGE=database`.
Use `AUTH_RATE_LIMIT_ENABLED=false` only for controlled local troubleshooting.
Forwarded client IP headers are ignored unless both settings are configured:

```text
AUTH_TRUST_PROXY_HEADERS=true
AUTH_TRUSTED_PROXY_IPS=known-proxy-ip-or-cidr
```

## CSRF Protection

NeoApps protects every POST, PUT, PATCH, and DELETE request with a shared,
session-bound CSRF token. Tokens are injected into rendered unsafe forms and
the base shell automatically supplies `X-CSRF-Token` to same-origin unsafe
`fetch` requests. Logout is POST-only.

`CSRF_ENABLED` defaults to `true` and `CSRF_TOKEN_TTL_SECONDS` defaults to
`7200`. Test fixtures can opt into live validation with
`CSRF_PROTECT_TESTING=true`; production validation is always enabled unless
`CSRF_ENABLED=false` is explicitly configured for controlled troubleshooting.

Do not enable forwarded-header trust without the known production proxy list.

## NeoSektor Google Sheets Compatibility

During the NeoSektor transition, NeoGateway remains database-first. Google
Sheets mirroring is controlled by the in-app NeoSektor Settings page and is
stored in the NeoGateway database per gateway. The default state is OFF.

Credentials alone never enable writes, and `NEOSEKTOR_SHEETS_COMPAT_ENABLED`
is not used for runtime enablement. A Master or Grandmaster must explicitly
turn Google Sheets Compatibility ON in the app before NeoGateway-integrated
NeoSektor writes to the configured sheet.

When the setting is ON, the bridge uses:

```text
GOOGLE_SHEETS_ID
GOOGLE_SHEETS_TAB
GOOGLE_SERVICE_ACCOUNT_JSON
```

When the setting is OFF, NeoGateway database updates continue normally and the
bridge does not construct a Google Sheets client or call Google APIs. The bridge
only writes the established standalone Live Counts cells after a successful
NeoGateway database commit; it does not read from Sheets or write during page
loads, polling, or refreshes. The standalone sheet has no cell for
NeoGateway-only Discharge events or the custom down-timer value, so the bridge
does not alter that sheet layout.

## Production Bootstrap

Database bootstrap is an idempotent manual/deployment step, not a web-worker or
Render Free Build Command step. Paid Render services can use the Pre-Deploy
Command; Render Free schema changes require the one-time command documented in
[`docs/deployment/render.md`](docs/deployment/render.md). Set these environment
variables for the bootstrap command:

```text
BOOTSTRAP_ADMIN_USERNAME
BOOTSTRAP_ADMIN_EMAIL
BOOTSTRAP_ADMIN_PASSWORD
```

The bootstrap creates tables, ensures the RFD gateway and active NeoNodes exist, marks the bootstrap admin email verified, grants approved RFD access, and grants Grandmaster node access. Running it more than once updates missing required seed data without creating duplicates. The bootstrap admin password is only applied when the bootstrap admin user is created for the first time. Later runs preserve the existing password while repairing missing gateway access or node roles.

## Local Development

Run these commands from the project folder:

```powershell
cd C:\DevProj\NeoApps
.\.venv\Scripts\python.exe scripts\seed_dev_user.py
.\.venv\Scripts\python.exe run.py
```

The canonical local launcher is `run.py`.

Do not use `app.py`; this project uses the `app/` package for the Flask app factory, and there is intentionally no root `app.py` launcher.

## Local URLs

Login / landing hub:

```text
http://127.0.0.1:5000/login
```

NeoGateway landing/login hub:

```text
http://127.0.0.1:5000/
```

NeoMotherBrain module:

```text
http://127.0.0.1:5000/motherbrain
```

Nightly Operations:

```text
http://127.0.0.1:5000/motherbrain/operations
```

Master Schedule:

```text
http://127.0.0.1:5000/motherbrain/master-schedule
```

Grandmaster User Management:

```text
http://127.0.0.1:5000/admin/users
```

## Local Dev Login

The local development seed script creates or updates a Grandmaster account:

```text
Username: Kessler
Password: LocalDevPassphrase2026!
```

To use a different local password, set `NEOAPPS_DEV_GRANDMASTER_PASSWORD` before running the seed script. Passwords must meet the NeoApps 12-128 character policy.

## Codex Result Summary Format

When reporting implementation results for ChatGPT handoff, use this copy-friendly format:

```text
Implemented and pushed:
Commit: <hash> - <title>

Changed files:
- path
- path

What changed:
- short bullet
- short bullet

Verification:
- command/result
- command/result
- browser checks if any

Notes:
- untracked files left untouched, if relevant
- anything intentionally deferred or not done
```
