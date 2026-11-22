"""Child entrypoint. Receives only one disposable restricted SCRAM login."""
from contextlib import closing
import os
import sys

import psycopg2
from sqlalchemy import text
from sqlalchemy.engine import make_url

from run_local import DBNAME, FALLBACK, MIGRATOR, RUNTIME
from app import create_app
from app.extensions import db


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
        db.session.remove()
        db.engine.dispose()


if __name__ == "__main__":
    main()
