# NeoApps / NeoRFD

NeoApps is the unified Flask platform for operational tools. NeoRFD is the current default Rockford Air Hub gateway workspace, and NeoMotherBrain is the operations core module inside NeoRFD.

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

Login:

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

## Local Dev Login

The local development seed script creates or updates a Grandmaster account:

```text
Username: Kessler
Password: 1313
```

To use a different local password, set `NEOAPPS_DEV_GRANDMASTER_PASSWORD` before running the seed script.
