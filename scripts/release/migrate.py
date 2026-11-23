"""Run the reviewed release tool with only its necessary inputs. Keep provider logs public-safe."""
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

from psycopg2.extensions import parse_dsn


def run(env, execute=subprocess.run):
    revision = env["GITHUB_SHA"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or env["APPROVED_SHA"] != revision:
        raise ValueError("Expected exact release SHA")
    dsn = parse_dsn(env["DATABASE_URL"])
    if (not dsn.get("host") or dsn.get("sslmode") != "verify-full"
            or dsn.get("user") != env["RELEASE_MIGRATOR"] or dsn.get("dbname") != env["RELEASE_DATABASE"]):
        raise ValueError("Expected TLS-verified dedicated migration target")
    root = Path(__file__).resolve().parents[2]
    child_env = {k: env[k] for k in ("PATH", "HOME", "LANG", "SYSTEMROOT", "WINDIR") if k in env}
    if os.name == "nt":
        # Libraries expect these directories on Windows. Use the isolated release home.
        child_env.update(APPDATA=child_env["HOME"], USERPROFILE=child_env["HOME"])
    # The CLI creates a Flask app context but serves no sessions. Keep the live signing key on Render.
    child_env.update(DATABASE_URL=env["DATABASE_URL"], SECRET_KEY=secrets.token_urlsafe(48),
                     PYTHONDONTWRITEBYTECODE="1", FLASK_SKIP_DOTENV="1",
                     NEOAPPS_EXISTING_BOOTSTRAP_ONLY="1", DATABASE_STARTUP_RETRY_ATTEMPTS="1")
    for key in ("BOOTSTRAP_ADMIN_USERNAME", "BOOTSTRAP_ADMIN_EMAIL"):
        if not env.get(key):
            raise ValueError("Pin the existing bootstrap identity")
        child_env[key] = env[key]
    cmd = [sys.executable, "scripts/runtime_database_access.py", "--apply", "--release", revision]
    for flag, setting in (("database", "DATABASE"), ("schema", "SCHEMA"), ("migrator", "MIGRATOR"),
                          ("runtime", "RUNTIME"), ("standby", "STANDBY")):
        cmd.extend(["--" + flag, env["RELEASE_" + setting]])
    # The application bootstrap can log user details or SQL diagnostics. Public CI receives only the outcome.
    execute(cmd, cwd=root, env=child_env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    try:
        run(os.environ)
    except Exception:
        sys.exit("Migration or verification failed. Inspect the database privately before retrying. Render remains gated.")
    print("Verified migration and both restricted policies. Exact-SHA receipt committed.")
