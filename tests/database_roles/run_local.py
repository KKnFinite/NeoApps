"""Start a disposable loopback SCRAM cluster and run the role acceptance suite.

Usage: python tests/database_roles/run_local.py --bin PATH/TO/POSTGRESQL/bin
No network service or existing database URL is used.
"""
import argparse
from contextlib import closing
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile

import psycopg2
from psycopg2 import sql

ROOT = Path(__file__).resolve().parents[2]
DBNAME = "neostaffing_test_acl"
MIGRATOR = "fixture_migrator"
RUNTIME = "fixture_runtime"
FALLBACK = "fixture_previous_runtime"
LEGACY = "fixture_prior_owner"


def process_env(home):
    env = {k: v for k, v in os.environ.items()
           if k.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT",
                            "LD_LIBRARY_PATH", "LANG", "LC_ALL"}}
    env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home), TMP=str(home),
               TEMP=str(home), TMPDIR=str(home), PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1",
               NEOAPPS_ENV="testing", FLASK_SKIP_DOTENV="1", DATABASE_URL="sqlite:///:memory:")
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin", type=Path, required=True)
    parser.add_argument("cases", nargs="*")
    args = parser.parse_args()
    binary = args.bin.resolve(strict=True)
    temp_root = Path(tempfile.gettempdir()).resolve()
    with tempfile.TemporaryDirectory(prefix="neoapps_acl_", dir=temp_root) as scratch:
        home = Path(scratch).resolve()
        if home.parent != temp_root or not home.name.startswith("neoapps_acl_"):
            raise RuntimeError("Unexpected disposable cluster path")
        data = home / "data"
        env = process_env(home)
        passwords = {role: secrets.token_urlsafe(32) for role in ("fixture_admin", MIGRATOR, RUNTIME, FALLBACK, LEGACY)}
        pwfile = home / "init.pass"
        pwfile.write_text(passwords["fixture_admin"] + "\n", encoding="ascii")
        def pg(command, *arguments):
            subprocess.run([str(binary / command), *map(str, arguments)], check=True, env=env,
                           cwd=home, timeout=120, stdout=subprocess.DEVNULL)
        pg("initdb", "-D", data, "-U", "fixture_admin", "--encoding=UTF8", "--locale=C",
           "--auth-host=scram-sha-256", "--auth-local=scram-sha-256", "--pwfile", pwfile)
        pwfile.unlink()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        pg("pg_ctl", "-D", data, "-l", home/"server.log", "-o", f"-h 127.0.0.1 -p {port}", "-w", "start")
        try:
            def url(user, database=DBNAME):
                return f"postgresql://{user}:{passwords[user]}@127.0.0.1:{port}/{database}"
            with closing(psycopg2.connect(url("fixture_admin", "postgres"))) as conn:
                conn.autocommit = True
                with conn.cursor() as cur:
                    for role in (MIGRATOR, RUNTIME, FALLBACK, LEGACY):
                        cur.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                                            "NOREPLICATION NOBYPASSRLS PASSWORD %s").format(sql.Identifier(role)),
                                    (passwords[role],))
                    cur.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(MIGRATOR), sql.Identifier(LEGACY)))
                    for database in (DBNAME, DBNAME + "_paths"):
                        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
            paths = [case for case in args.cases if case != "boundary"]
            groups = []
            if not args.cases or "boundary" in args.cases:
                groups.append((DBNAME, ["boundary"]))
            if not args.cases or paths:
                groups.append((DBNAME + "_paths", paths or ["paths"]))
            status = 0
            for database, cases in groups:
                with closing(psycopg2.connect(url("fixture_admin", database))) as conn, conn, conn.cursor() as cur:
                    cur.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
                    for role in (MIGRATOR, RUNTIME, FALLBACK, LEGACY):
                        cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), sql.Identifier(role)))
                    cur.execute(sql.SQL("GRANT USAGE,CREATE ON SCHEMA public TO {}").format(sql.Identifier(MIGRATOR)))
                    if database.endswith("_paths"):
                        # Upstream test fixtures create disposable schemas. The
                        # deployment rehearsal above needs only the app schema.
                        cur.execute(sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(
                            sql.Identifier(database), sql.Identifier(MIGRATOR)))
                env.update(NEOSTAFFING_TEST_POSTGRES_URL=url(MIGRATOR, database), ROLE_TEST_RUNTIME_URL=url(RUNTIME, database),
                           ROLE_TEST_FALLBACK_URL=url(FALLBACK, database))
                if database == DBNAME:
                    env['ROLE_TEST_PRIOR_URL'] = url(LEGACY, database)
                else:
                    env.pop('ROLE_TEST_PRIOR_URL', None)
                status |= subprocess.run([sys.executable, "tests/database_roles/acceptance.py", *cases],
                                         env=env, cwd=ROOT, check=False).returncode
            return status
        finally:
            pg("pg_ctl", "-D", data, "-m", "fast", "-w", "stop")


if __name__ == "__main__":
    sys.exit(main())
