# NeoGateway / NeoApps / NeoRFD

NeoGateway is the public platform brand. NeoApps is the technical Flask framework powering the operational tools. NeoRFD is the current default Rockford Air Hub gateway workspace, and NeoMotherBrain is the operations core module inside NeoRFD.

## Access Model

NeoApps authentication is global. NeoRFD is the current default gateway, so RFD is selected automatically and no gateway selector is shown yet.

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

## Production Bootstrap

After deploying with a fresh Neon database, run the safe idempotent bootstrap:

```powershell
python scripts\bootstrap_database.py
```

Set these environment variables in Render before running it:

```text
BOOTSTRAP_ADMIN_USERNAME
BOOTSTRAP_ADMIN_EMAIL
BOOTSTRAP_ADMIN_PASSWORD
```

The bootstrap creates tables, ensures the RFD gateway and active NeoNodes exist, marks the bootstrap admin email verified, grants approved RFD access, and grants Grandmaster node access. Running it more than once updates missing required seed data without creating duplicates.

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

NeoRFD landing/login hub:

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
Password: 1313
```

To use a different local password, set `NEOAPPS_DEV_GRANDMASTER_PASSWORD` before running the seed script.
