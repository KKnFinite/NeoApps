# Render Deployment

Gunicorn imports `run:app` without opening PostgreSQL connections or performing
any schema/bootstrap work, including targeted compatibility ensures. It can
construct the application while Neon is unavailable and bind to Render's
assigned port promptly. Do not use `AUTO_BOOTSTRAP_DATABASE=true` for a Render
web process. No schema repair runs on first request or during polling.

## Render Commands

This repository does not declare a Render Blueprint or repository-managed
pre-deploy hook. `Procfile` defines only the web start command. Editing that file
or this document does **not** configure a release step in Render.
On the existing NeoApps service, set **Settings → Build & Deploy → Pre-Deploy
Command** to `python scripts/bootstrap_database.py`. Keep the start command
unchanged. Confirm the setting is saved before shipping schema-dependent code.

For a paid Render web service with Pre-Deploy Commands, configure:

```text
Build Command: pip install -r requirements.txt
Pre-Deploy Command: python scripts/bootstrap_database.py
Start Command: gunicorn run:app --bind 0.0.0.0:$PORT
```

Render runs the pre-deploy command once after the build and before the new web
service starts. A failed bootstrap stops the deployment, so the new web process
never serves against an incomplete schema.

Render Free does not support Pre-Deploy Commands. Keep schema/bootstrap work
out of its Build Command so a transient database failure cannot block the
deployment build or web port bind:

```text
Build Command: pip install -r requirements.txt
Pre-Deploy Command: (leave blank)
Start Command: gunicorn run:app --bind 0.0.0.0:$PORT
```

For Free-plan services, run the bootstrap from the target release checkout
**before deploying** code that requires new columns, using a trusted machine or
CI runner configured with production `DATABASE_URL` and bootstrap credentials.
Do not push schema-dependent code to an automatically deployed branch until
this release prerequisite succeeds. Free-plan manual ordering is not an
automatic pre-deploy safety gate; use a supported paid service's Pre-Deploy
Command when that guarantee is required. Do not put bootstrap in Gunicorn startup.

```powershell
$env:DATABASE_URL = "<production Neon DATABASE_URL>"
$env:BOOTSTRAP_ADMIN_USERNAME = "<existing bootstrap username>"
$env:BOOTSTRAP_ADMIN_EMAIL = "<existing bootstrap email>"
$env:BOOTSTRAP_ADMIN_PASSWORD = "<configured bootstrap password>"
python scripts/bootstrap_database.py
```

The command is idempotent and uses four bounded retry attempts with one, two,
and four-second backoff delays by default. It sets a five-second connection and
pool timeout plus five-second lock and fifteen-second statement timeouts. Every
phase and retry emits a safe, flushed log line. Any genuine schema error exits
nonzero and does not retry.

The canonical command remains `python scripts/bootstrap_database.py`. Its order
is: create missing tables, synchronize additive schema and legacy defaults,
verify the SPEAR contract, seed gateway/nodes and permissions, seed Sheets/live
poll settings, repair bootstrap admin/access, then commit. Schema synchronization
includes mission-aware Door Pull columns and legacy boolean defaults, SPEAR
settings/assignment columns and audit/calibration-reset tables, and Rain
integration authority columns and fuel-authority tables. It does not depend on
a running web process. `init_db.py` is local SQLite tooling, not deployment tooling.

## Repairing the Ballmat missing-column incident

### Conductor-owned spotter modes (2026-09-09)

The conductor mode-authority release adds three nullable/defaulted columns to
the existing `neosektor_ballmat_counts` table: `mode_version` (integer, default
0), `pending_mode` (nullable integer), and `mode_request_version` (integer,
default 0). The canonical bootstrap above adds them idempotently before the
new code serves traffic. Existing modes, RIGHT allocations and totals remain
unchanged; no data backfill or separate LEFT totals are needed. Normal web
startup and polling must not run schema synchronization.

### Original dual-spotter columns

Commit `31152a8` requires four additive columns on `neosektor_ballmat_counts`:
`spotter_mode` (integer, default 1), `right_first`, `right_second`, and
`right_open` (integers, default 0). Both schema-sync dialect maps already contain
them. No replacement ALTER script is required.

In the production service's authorized Render Shell, from the application root
at `31152a8` or later, run:

```sh
python scripts/bootstrap_database.py
```

Use the existing production environment and bootstrap credentials; do not paste
credentials into logs or documentation. Require exit status zero and the
`schema and seed data ready` completion message. Running it again is safe.
Then verify using the authorized database connection (read-only query):

```sql
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = current_schema()
  AND table_name = 'neosektor_ballmat_counts'
  AND column_name IN ('spotter_mode', 'right_first', 'right_second', 'right_open')
ORDER BY column_name;
```

Require all four rows, then check authenticated EBM, WBM, Live Counts and Tunnel
Conductor and confirm no new `UndefinedColumn` errors in their request logs.
`/healthz` alone cannot verify this repair: it intentionally never queries the DB.
Saving Pre-Deploy Command prevents subsequent omissions; it does not repair an
already-running service by itself. Do not claim recovery from local tests.

Render documents pre-deploy ordering and paid-service availability at
[Deploying on Render](https://render.com/docs/deploys#pre-deploy-command).

## Process Liveness

Recommended Render Health Check Path: `/healthz`

Configure this path in the Render dashboard; this repository change does not
automatically change the service setting. GET and HEAD return HTTP 200 with
`Cache-Control: no-store`. This is process/web-server liveness only, **not database
readiness**. It intentionally does not query Neon, load the authenticated user,
or call Google. Database availability is handled by normal application request
error behavior, not by continuously waking Neon with health-check queries.

Keep local development on `python run.py`; `run.py` also reads `PORT` when run
directly, but production should use Gunicorn.
