"""Child entrypoint. Receives only one disposable restricted SCRAM login."""
from contextlib import closing
from datetime import timedelta
import os
import sys
import json

import psycopg2
from psycopg2 import sql
from sqlalchemy import text
from sqlalchemy.engine import make_url

from run_local import DBNAME, FALLBACK, MIGRATOR, RUNTIME
from app import create_app
from app.extensions import db
from scripts.runtime_database_access import COLUMN_MANIFEST, read_manifest


def check_statement_permissions(conn):
    grants = read_manifest()
    grants['neoapps_reviewed_deployments'] = {'SELECT'}
    column_grants = json.loads(COLUMN_MANIFEST.read_text(encoding='utf-8'))
    allowed = denied = 0
    checks = []
    with conn, conn.cursor() as cur:
        cur.execute("""SELECT table_name,column_name FROM information_schema.columns
                       WHERE table_schema=current_schema() ORDER BY table_name,ordinal_position""")
        columns = {}
        for table, column in cur:
            columns.setdefault(table, []).append(column)
        for table, verbs in sorted(grants.items()):
            target = sql.Identifier(table)
            assignments = set(column_grants.get(table, '').split())
            first_column = columns[table][0]
            statements = [
                ('SELECT', None, 'SELECT' in verbs, sql.SQL('SELECT * FROM {}').format(target)),
                ('INSERT', None, 'INSERT' in verbs, sql.SQL('INSERT INTO {} DEFAULT VALUES').format(target)),
                ('DELETE', None, 'DELETE' in verbs, sql.SQL('DELETE FROM {} WHERE false').format(target))]
            for column in columns[table] if assignments else (first_column,):
                statements.append(('UPDATE', column, 'UPDATE' in verbs or column in assignments,
                    sql.SQL('UPDATE {} SET {}=DEFAULT WHERE false').format(target, sql.Identifier(column))))
            for verb, column, permitted, statement in statements:
                cur.execute('SAVEPOINT acl_probe')
                try:
                    # EXPLAIN checks the real executor's permissions without
                    # running a plan, firing triggers, or allocating IDs.
                    cur.execute(sql.SQL('EXPLAIN ') + statement)
                except psycopg2.errors.InsufficientPrivilege:
                    assert not permitted, f'Unexpected denial for {table}'
                    denied += 1
                else:
                    assert permitted, f'Unexpected permission for {table}'
                    allowed += 1
                finally:
                    cur.execute('ROLLBACK TO SAVEPOINT acl_probe')
                    cur.execute('RELEASE SAVEPOINT acl_probe')
                checks.append(dict(table=table, verb=verb, column=column, allowed=permitted))
    if path := os.getenv('ROLE_ACL_EVIDENCE'):
        with open(path, 'a', encoding='utf-8') as file:
            print(json.dumps(dict(role=conn.info.user, statements=checks)), file=file)
    print(f'PostgreSQL statement matrix: {allowed} allowed, {denied} denied', flush=True)


def main():
    role = sys.argv[1]
    url = make_url(os.environ["DATABASE_URL"])
    assert role in (RUNTIME, FALLBACK)
    assert (url.username, url.host, url.database) == (role, "127.0.0.1", DBNAME)
    assert not set(os.environ).intersection({"NEOSTAFFING_TEST_POSTGRES_URL", "ROLE_TEST_FALLBACK_URL",
        "ROLE_TEST_RUNTIME_URL", "ROLE_TEST_PRIOR_URL", "PGPASSWORD", "PGPASSFILE", "PGSERVICE", "BOOTSTRAP_ADMIN_PASSWORD"})
    # A normal SCRAM login under the other identity cannot use this credential.
    try:
        other = psycopg2.connect(url.set(username=MIGRATOR).render_as_string(hide_password=False))
    except psycopg2.OperationalError as exc:
        if "password authentication failed" not in str(exc):
            raise RuntimeError("Expected SCRAM denial") from None
    else:
        other.close()
        raise AssertionError("Runtime credential authenticated as the migrator")
    with closing(psycopg2.connect(os.environ["DATABASE_URL"])) as conn:
        if len(sys.argv) == 2:
            check_statement_permissions(conn)
        with conn, conn.cursor() as cur:
            cur.execute("SELECT session_user,current_user")
            assert cur.fetchone() == (role, role)
            cur.execute("SELECT count(*) FROM account_security_events")
            assert cur.fetchone()[0] > 0
        for command in (
            "CREATE TABLE fixture_unapproved_ddl (id integer)",
            "ALTER TABLE fixture_migration_private OWNER TO CURRENT_USER",
            "SELECT * FROM fixture_migration_private",
            "DELETE FROM account_security_events WHERE false",
            "UPDATE neoapps_reviewed_deployments SET revision=revision WHERE false",
            "UPDATE account_preferences SET user_id=user_id WHERE false",
            "UPDATE staffing_shift_flow_plans SET staffing_person_id=staffing_person_id WHERE false",
            "UPDATE staffing_units SET unit_type=unit_type WHERE false",
            "UPDATE neosubzero_trucks SET gateway_id=gateway_id WHERE false",
            "CREATE TEMPORARY TABLE fixture_unapproved_temp(id integer)",
            "TRUNCATE fixture_migration_private",
        ):
            try:
                with conn, conn.cursor() as cur:
                    cur.execute(command)
            except psycopg2.errors.InsufficientPrivilege:
                continue
            raise AssertionError("Expected SQLSTATE 42501 for the isolated privilege check")
    app = create_app(auto_bootstrap=False)
    with app.app_context():
        assert db.session.scalar(text("SELECT current_user")) == role
        assert db.session.scalar(text("SELECT count(*) FROM users")) > 0
        protected = app.test_client().get('/accounts')
        assert protected.status_code in (302, 401, 403)
        if len(sys.argv) == 3:
            from app.models import User
            from app.models.staffing_employee_recovery import StaffingEmployeeRecovery as Backup, StaffingEmployeeRecoveryAudit as Audit
            from app.services.neostaffing_employee_recovery import now_utc, seal
            from app.services.neostaffing_employee_recovery_worker import recovery_cycle
            marker = 'fbc384ab-d811-49f3-bcf3-ae463ec3e095'
            if sys.argv[2] == 'stage':
                due = now_utc() - timedelta(seconds=1)
                db.session.add(Backup(id=marker, person_id=90001, actor_id=User.query.first().id,
                                      created_at=due-timedelta(days=1), expires_at=due, state='deleted',
                                      encrypted_payload=seal(dict(files=[]))))
                db.session.commit()
            else:
                assert sys.argv[2] == 'recover'
                recovery_cycle()
                row = db.session.get(Backup, marker)
                assert row.state == 'purged' and row.encrypted_payload is None
                assert Audit.query.filter_by(recovery_id=marker, action='purged').count() == 1
        db.session.remove()
        db.engine.dispose()


if __name__ == "__main__":
    main()
