"""Start a disposable loopback SCRAM cluster and run the role acceptance suite.

Usage: python tests/database_roles/run_local.py --bin PATH/TO/POSTGRESQL/bin
No network service or existing database URL is used.
"""
import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import ipaddress
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile

import psycopg2
from psycopg2 import sql
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.review_runtime_temp import prepare

ROOT = Path(__file__).resolve().parents[2]
DBNAME = "neostaffing_test_acl"
MIGRATOR = "fixture_migrator"
RUNTIME = "fixture_runtime"
FALLBACK = "fixture_previous_runtime"
LEGACY = "fixture_prior_owner"


def enable_fixture_tls(data):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .sign(key, hashes.SHA256()))
    with (data / "server.key").open("wb") as dest:
        (data / "server.key").chmod(0o600)
        dest.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()))
    (data / "server.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))


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
    parser.add_argument("--matrix", type=Path, help="Write per-case PostgreSQL operation coverage as JSON lines")
    parser.add_argument("--workloads", type=Path, help="Additional real application unittest IDs, one per line")
    parser.add_argument("cases", nargs="*")
    args = parser.parse_args()
    if not args.cases and not args.workloads:
        args.cases = ['boundary', 'paths']
        args.workloads = Path(__file__).with_name('workloads.txt')
    binary = args.bin.resolve(strict=True)
    temp_root = Path(tempfile.gettempdir()).resolve()
    with tempfile.TemporaryDirectory(prefix="neoapps_acl_", dir=temp_root) as scratch:
        home = Path(scratch).resolve()
        if home.parent != temp_root or not home.name.startswith("neoapps_acl_"):
            raise RuntimeError("Unexpected disposable cluster path")
        data = home / "data"
        env = process_env(home)
        if args.workloads:
            env["ROLE_WORKLOADS"] = str(args.workloads.resolve(strict=True))
            args.cases.append("catalog_paths")
        if args.matrix:
            matrix = args.matrix.resolve()
            matrix.touch(exist_ok=False)
            Path(str(matrix) + '.statements.jsonl').touch(exist_ok=False)
            env["ROLE_TEST_MATRIX"] = str(matrix)
        passwords = {role: secrets.token_urlsafe(32) for role in ("fixture_admin", MIGRATOR, RUNTIME, FALLBACK, LEGACY)}
        pwfile = home / "init.pass"
        pwfile.write_text(passwords["fixture_admin"] + "\n", encoding="ascii")
        def pg(command, *arguments):
            subprocess.run([str(binary / command), *map(str, arguments)], check=True, env=env,
                           cwd=home, timeout=120, stdout=subprocess.DEVNULL)
        pg("initdb", "-D", data, "-U", "fixture_admin", "--encoding=UTF8", "--locale=C",
           "--auth-host=scram-sha-256", "--auth-local=scram-sha-256", "--pwfile", pwfile)
        pwfile.unlink()
        enable_fixture_tls(data)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        pg("pg_ctl", "-D", data, "-l", home/"server.log", "-o", f"-h 127.0.0.1 -p {port} -c ssl=on", "-w", "start")
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
                    # Start with PostgreSQL's real default, as on the hosted
                    # database. The reviewed consumer retains TEMP and a new
                    # runtime retains CONNECT while losing TEMP.
                    had_public_temp = prepare(cur, database, [MIGRATOR], apply=True)
                    if not had_public_temp or prepare(cur, database, [MIGRATOR]):
                        raise RuntimeError("Database TEMP preparation did not narrow the default")
                    cur.execute("SELECT has_database_privilege(%s,current_database(),'TEMPORARY'),"
                                "has_database_privilege(%s,current_database(),'TEMPORARY'),"
                                "has_database_privilege(%s,current_database(),'CONNECT')", (MIGRATOR,RUNTIME,RUNTIME))
                    if cur.fetchone() != (True, False, True):
                        raise RuntimeError("Database TEMP exception or runtime CONNECT differs from policy")
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
                actors = [MIGRATOR] if cases == ['boundary'] else [MIGRATOR, RUNTIME, FALLBACK]
                for actor in actors:
                    if cases == ['catalog_paths'] and actor != RUNTIME:
                        continue
                    status |= subprocess.run([sys.executable, "tests/database_roles/acceptance.py", *cases],
                                             env=dict(env, ROLE_TEST_ACTOR=actor), cwd=ROOT, check=False).returncode
            return status
        finally:
            # The uniquely named scratch cluster is discarded immediately
            # after all test clients exit. A final disk checkpoint is unused.
            pg("pg_ctl", "-D", data, "-m", "immediate", "-w", "stop")


if __name__ == "__main__":
    sys.exit(main())
