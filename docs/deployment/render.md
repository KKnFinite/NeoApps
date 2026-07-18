# Render Deployment

NeoApps performs database schema synchronization and bootstrap before the web
process starts. Gunicorn imports `run:app` without database bootstrap work so it
can bind to Render's assigned port promptly. Do not use
`AUTO_BOOTSTRAP_DATABASE=true` for a Render web process.

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

Render Free does not support Pre-Deploy Commands. Configure the same safe
sequence in the build instead:

```text
Build Command: pip install -r requirements.txt && python scripts/bootstrap_database.py
Pre-Deploy Command: (leave blank)
Start Command: gunicorn run:app --bind 0.0.0.0:$PORT
```

The bootstrap is idempotent and has the bounded PostgreSQL connection retry
policy. Its defaults are five attempts with one, two, four, and eight-second
backoff delays, for fifteen seconds of scheduled delay total. Connection setup
may add database-side wait time, but that happens before deployment rather than
during Render's web port scan. Each attempt writes a safe action/attempt log
line; Python logging flushes emitted records through the configured handler.

Keep local development on `python run.py`; `run.py` also reads `PORT` when run
directly, but production should use Gunicorn.
