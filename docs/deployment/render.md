# Render Deployment

NeoApps includes a `Procfile` with the Render-safe web command:

```text
web: gunicorn run:app --bind 0.0.0.0:$PORT
```

If Render logs show the service starting with only:

```text
gunicorn run:app
```

then the service is using a dashboard Start Command override instead of the repo
`Procfile`. Update the Render web service Start Command to:

```text
gunicorn run:app --bind 0.0.0.0:$PORT
```

Render web services must listen on `0.0.0.0` and the assigned `PORT`. A command
that does not bind to `$PORT` can start Gunicorn on its default local port and
then fail Render's port scan with:

```text
Port scan timeout reached, no open ports detected
```

Keep local development on `python run.py`; `run.py` also reads `PORT` when run
directly, but production should use Gunicorn.
