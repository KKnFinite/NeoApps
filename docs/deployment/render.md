# Render Deployment

Gunicorn imports `run:app` without opening PostgreSQL connections or performing
any schema/bootstrap work, including targeted compatibility ensures. It can
construct the application while Neon is unavailable and bind to Render's
assigned port promptly. Do not use `AUTO_BOOTSTRAP_DATABASE=true` for a Render
web process. No schema repair runs on first request or during polling.

## Render Commands

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

After a Free-plan deployment that changes schema, run the one-time manual
bootstrap from a trusted machine or CI runner configured with the production
`DATABASE_URL` and bootstrap credentials:

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
