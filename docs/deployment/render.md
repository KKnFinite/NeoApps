# Render Deployment

Gunicorn imports `run:app` without database bootstrap work so it can bind to
Render's assigned port promptly. Do not use `AUTO_BOOTSTRAP_DATABASE=true` for
a Render web process.

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

Keep local development on `python run.py`; `run.py` also reads `PORT` when run
directly, but production should use Gunicorn.
