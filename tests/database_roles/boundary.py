"""Production-style release ordering, clean child process, restricted login and rollback."""
from contextlib import closing
from hashlib import sha256
import os
from pathlib import Path
import secrets
import subprocess
import sys
import unittest

import psycopg2
from psycopg2 import sql
from sqlalchemy.engine import make_url

from run_local import DBNAME, FALLBACK, LEGACY, MIGRATOR, RUNTIME, ROOT, process_env

TOOL = "scripts/runtime_database_access.py"


class RoleBoundary(unittest.TestCase):
    def setUp(self):
        self.schema = "public"
        self.release = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
        self.owner = self.url("NEOSTAFFING_TEST_POSTGRES_URL")
        self.web = self.url("ROLE_TEST_RUNTIME_URL")
        self.assertEqual(make_url(self.owner).database, DBNAME)
        # No application schema exists until the actual off-service bootstrap.
        self.owner_env = process_env(Path(os.environ["HOME"]))
        self.owner_env.update(DATABASE_URL=self.owner, BOOTSTRAP_ADMIN_PASSWORD=secrets.token_urlsafe(26))
        self.web_env = process_env(Path(os.environ["HOME"]))
        self.web_env.update(DATABASE_URL=self.web, RENDER_GIT_COMMIT=self.release)
        self.web_env['SECRET_KEY'] = secrets.token_urlsafe(32)
        self.legacy = self.url("ROLE_TEST_PRIOR_URL")
        self.legacy_env = dict(self.owner_env, DATABASE_URL=self.legacy)
        self.invoke("scripts/bootstrap_database.py", self.legacy_env)
        self.args = ["--runtime", RUNTIME, "--schema", self.schema, "--database", DBNAME]
        self.apply_args = [*self.args, "--apply", "--release", self.release,
                           "--migrator", MIGRATOR, "--standby", FALLBACK]

    def url(self, key):
        return make_url(os.environ[key]).update_query_dict(
            {'options': '-csearch_path='+self.schema}).render_as_string(hide_password=False)

    def run_command(self, script, env, *args):
        return subprocess.run([sys.executable, script, *args], env=env, cwd=ROOT,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=100, text=True)

    def invoke(self, script, env, *args, expected=None):
        result = self.run_command(script, env, *args)
        if expected:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(expected, result.stderr)
        else:
            # Keep DSNs, SQL parameters and fixture passwords out of failure reports.
            self.assertEqual(result.returncode, 0, result.stderr.splitlines()[-1:] or script)
        return result.stdout

    def fingerprint(self, conn):
        with conn, conn.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cur.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename", (self.schema,))
            result = {}
            for (name,) in cur.fetchall():
                if name == 'neoapps_reviewed_deployments':
                    continue
                cur.execute(sql.SQL("SELECT to_jsonb(t)::text FROM {}.{} t ORDER BY to_jsonb(t)::text COLLATE \"C\"")
                            .format(sql.Identifier(self.schema), sql.Identifier(name)))
                digest = sha256()
                count = 0
                for (row,) in cur:
                    data = row.encode('utf8')
                    digest.update(len(data).to_bytes(8, 'big') + data)
                    count += 1
                result[name] = (count, digest.hexdigest())
            self.assertGreater(len(result), 170)
            self.assertGreater(sum(n for n, _ in result.values()), 0)
            return result

    def test_isolated_migration_runtime_and_rollback(self):
        with closing(psycopg2.connect(self.legacy)) as old, old, old.cursor() as cur:
            cur.execute("CREATE TABLE fixture_migration_private (private_value text)")
            cur.execute("INSERT INTO fixture_migration_private VALUES ('fixture')")
            cur.execute("INSERT INTO account_security_events (actor_user_id,subject_user_id,action,details,created_at) "
                        "SELECT id,id,'isolated-rehearsal','{}',now() FROM users ORDER BY id LIMIT 1")
            self.assertEqual(cur.rowcount, 1)
        with closing(psycopg2.connect(self.legacy)) as original:
            before = self.fingerprint(original)
        plan = str(Path(os.environ["HOME"]) / "reviewed-objects.json")
        transfer_args = ["--database", DBNAME, "--schema", self.schema, "--previous", LEGACY, "--target", MIGRATOR]
        transfer_tool = "scripts/review_database_ownership.py"
        self.invoke(transfer_tool, self.legacy_env, *transfer_args, "--inventory", plan)
        with closing(psycopg2.connect(self.legacy)) as original, original, original.cursor() as cur:
            cur.execute("CREATE TABLE fixture_post_review (id integer)")
        self.invoke(transfer_tool, self.legacy_env, *transfer_args, "--apply-reviewed", plan,
                    expected="Owner-reviewed catalog is stale")
        with closing(psycopg2.connect(self.legacy)) as original, original, original.cursor() as cur:
            cur.execute("DROP TABLE fixture_post_review")
        self.invoke(transfer_tool, self.legacy_env, *transfer_args, "--apply-reviewed", plan)
        with closing(psycopg2.connect(self.owner)) as old:
            self.assertEqual(self.fingerprint(old), before)
            self.invoke("scripts/bootstrap_database.py", self.owner_env)
            after_bootstrap = self.fingerprint(old)
            self.assertEqual([name for name in before if before[name] != after_bootstrap[name]], [],
                             "Repeat bootstrap changed existing rows")
            # An unexpected PUBLIC grant must abort provisioning, including the
            # newly inserted release receipt. Reproduce drift on a fixture only.
            with old, old.cursor() as cur:
                cur.execute("GRANT SELECT (private_value) ON fixture_migration_private TO PUBLIC")
            self.invoke(TOOL, self.owner_env, *self.apply_args,
                        expected='Unreviewed or missing table privilege')
            with old, old.cursor() as cur:
                cur.execute("SELECT to_regclass('neoapps_reviewed_deployments')")
                self.assertIsNone(cur.fetchone()[0])
                cur.execute("REVOKE SELECT (private_value) ON fixture_migration_private FROM PUBLIC")
            self.invoke(TOOL, self.owner_env, *self.apply_args)
            alternate = Path(os.environ['HOME']) / 'alternate-checkout'
            # A self-contained checkout and independent home simulate the
            # second workstation without access to the primary checkout.
            subprocess.run(['git', 'init', '-q', str(alternate)], check=True)
            subprocess.run(['git', '-C', str(alternate), 'fetch', '--quiet', '--depth', '1',
                            ROOT.as_uri(), self.release], check=True)
            subprocess.run(['git', '-C', str(alternate), 'checkout', '--quiet', '--detach', self.release], check=True)
            self.assertFalse((alternate / '.git/objects/info/alternates').exists())
            custodial_home = Path(os.environ['HOME']) / 'alternate-home'
            custodial_home.mkdir()
            alternate_env = dict(process_env(custodial_home), PYTHONPATH=str(alternate),
                                 DATABASE_URL=self.owner, BOOTSTRAP_ADMIN_PASSWORD=self.owner_env['BOOTSTRAP_ADMIN_PASSWORD'])
            alternate_run = subprocess.run([sys.executable, TOOL, *self.apply_args], cwd=alternate,
                                           env=alternate_env, capture_output=True, text=True, timeout=150)
            self.assertEqual(alternate_run.returncode, 0, alternate_run.stderr.splitlines()[-1:])
            self.invoke(TOOL, self.web_env, *self.args)
            hosted = dict(self.web_env, RENDER='true', NEOAPPS_RUNTIME_DATABASE=DBNAME,
                          NEOAPPS_RUNTIME_SCHEMA=self.schema, NEOAPPS_RUNTIME_ROLE=RUNTIME)
            self.invoke("scripts/bootstrap_database.py", hosted)
            self.invoke("scripts/bootstrap_database.py", dict(hosted, DATABASE_URL=self.owner),
                        expected="Unexpected target database or grantor")
            self.invoke("scripts/bootstrap_database.py", dict(hosted, RENDER_GIT_COMMIT='0'*40),
                        expected="no off-service migration receipt")
            self.invoke("scripts/bootstrap_database.py", dict(hosted, BOOTSTRAP_ADMIN_PASSWORD='fixture'),
                        expected="Remove privileged credentials")
            with old, old.cursor() as cur:
                cur.execute("CREATE FUNCTION fixture_unreviewed() RETURNS integer LANGUAGE SQL AS 'SELECT 1'")
            self.invoke("scripts/bootstrap_database.py", hosted, expected="Unreviewed executable routine")
            with old, old.cursor() as cur:
                cur.execute("REVOKE EXECUTE ON FUNCTION fixture_unreviewed() FROM PUBLIC")
            self.invoke(TOOL, self.web_env, *self.args, '--release', '0'*40, expected='no off-service migration receipt')
            self.invoke(TOOL, self.owner_env, *self.args, '--release', self.release, expected='Unexpected target')
            self.invoke(TOOL, self.web_env, *self.apply_args, expected='Unexpected target')
            with old, old.cursor() as cur:
                cur.execute("ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO PUBLIC")
            self.invoke(TOOL, self.web_env, *self.args, expected='Default privileges')
            with old, old.cursor() as cur:
                cur.execute("ALTER DEFAULT PRIVILEGES REVOKE SELECT ON TABLES FROM PUBLIC")
                cur.execute("SELECT has_database_privilege(current_user,current_database(),'CREATE'), "
                            "has_schema_privilege(current_user,'public','CREATE'),"
                            "rolsuper OR rolcreaterole OR rolcreatedb OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
                self.assertEqual(cur.fetchone(), (False, True, False))
            print(self.invoke("tests/database_roles/runtime_probe.py", self.web_env, RUNTIME), flush=True)
            fallback_args = ["--runtime", FALLBACK, "--schema", self.schema, "--database", DBNAME]
            self.assertEqual(self.fingerprint(old), before)
        # Simulate rollback by draining the restricted connection and starting a
        # freshly authenticated process with the independently retained prior DSN.
        fallback_env = dict(self.web_env, DATABASE_URL=self.url("ROLE_TEST_FALLBACK_URL"))
        self.invoke(TOOL, fallback_env, *fallback_args)
        print(self.invoke("tests/database_roles/runtime_probe.py", fallback_env, FALLBACK), flush=True)
        with closing(psycopg2.connect(self.owner)) as restored:
            self.assertEqual(self.fingerprint(restored), before)
        self.invoke("tests/database_roles/runtime_probe.py", self.web_env, RUNTIME, "stage")
        self.invoke("tests/database_roles/runtime_probe.py", fallback_env, FALLBACK, "recover")
        self.invoke("tests/database_roles/runtime_probe.py", self.web_env, RUNTIME, "recover")
