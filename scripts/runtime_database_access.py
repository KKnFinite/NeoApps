"""Review/apply the versioned application ACL from an off-service migration session.

Default is read-only verification. No secret is accepted as a command-line argument.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import psycopg2
from psycopg2 import sql

MANIFEST = Path(__file__).with_name("runtime_database_grants.json")
COLUMN_MANIFEST = Path(__file__).with_name("runtime_update_columns.json")
DML = {"SELECT", "INSERT", "UPDATE", "DELETE"}
RECEIPT = "neoapps_reviewed_deployments"
FUNCTION_MANIFEST = Path(__file__).with_name("runtime_database_functions.json")


def read_manifest():
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    result = {}
    for name, actions in entries.items():
        grants = set(actions.split())
        if not grants or grants - DML:
            raise ValueError(f"Invalid grant for {name}")
        result[name] = grants
    return result


def check_principal(cursor, schema, role):
    cursor.execute("""
        SELECT rolcanlogin AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls),
               NOT EXISTS (SELECT FROM pg_auth_members WHERE member=r.oid),
               NOT has_database_privilege(oid,current_database(),'CREATE'),
               NOT has_database_privilege(oid,current_database(),'TEMPORARY'),
               has_database_privilege(oid,current_database(),'CONNECT')
               AND NOT has_database_privilege(oid,current_database(),'CONNECT WITH GRANT OPTION'),
               NOT EXISTS (SELECT FROM pg_namespace WHERE nspowner=r.oid
                           OR has_schema_privilege(r.oid,pg_namespace.oid,'CREATE')),
               NOT EXISTS (SELECT FROM pg_shdepend WHERE refclassid='pg_authid'::regclass
                           AND refobjid=r.oid AND deptype='o'),
               NOT EXISTS (SELECT FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                           WHERE p.prosecdef AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
                           AND has_schema_privilege(r.oid,n.oid,'USAGE')
                           AND has_function_privilege(r.oid,p.oid,'EXECUTE'))
        FROM pg_roles r WHERE rolname=%s
    """, (role,))
    checks = cursor.fetchone()
    if not checks or not all(checks):
        raise ValueError("Runtime role is missing or has unreviewed ownership, elevated rights, membership or executable definer routines")
    cursor.execute("SELECT oid FROM pg_namespace WHERE nspname=%s", (schema,))
    if not cursor.fetchone():
        raise ValueError("Schema does not exist")


def configure(cursor, schema, role, apply=False):
    manifest = read_manifest()
    update_columns = {table: columns.split() for table, columns in
                      json.loads(COLUMN_MANIFEST.read_text(encoding="utf-8")).items()}
    check_principal(cursor, schema, role)
    ident = sql.Identifier
    cursor.execute("""
        SELECT c.relname,c.oid,c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=%s AND c.relkind IN ('r','p','v','m','f','S')
    """, (schema,))
    objects = {name: (oid, kind) for name, oid, kind in cursor}
    if RECEIPT in objects:
        manifest[RECEIPT] = {"SELECT"}
    missing = set(manifest) - objects.keys()
    if missing:
        raise ValueError("Missing reviewed relations: " + ", ".join(sorted(missing)))
    for table in manifest:
        if objects[table][1] not in ('r', 'p'):
            raise ValueError(f"Unexpected relation type for {table}")
    cursor.execute("""
        SELECT seq.relname,tbl.relname FROM pg_depend d
        JOIN pg_class seq ON seq.oid=d.objid AND seq.relkind='S'
        JOIN pg_class tbl ON tbl.oid=d.refobjid AND tbl.relnamespace=seq.relnamespace
        WHERE d.classid='pg_class'::regclass AND d.refclassid='pg_class'::regclass
        AND d.deptype IN ('i','a') AND seq.relnamespace=%s::regnamespace
    """, (ident(schema).as_string(cursor.connection),))
    sequences = {seq for seq, table in cursor if "INSERT" in manifest.get(table, ())}
    if apply:
        cursor.execute("SELECT has_schema_privilege(%s,%s,'USAGE')", (role, schema))
        if not cursor.fetchone()[0]:
            cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(ident(schema), ident(role)))
        for table, actions in manifest.items():
            cursor.execute(sql.SQL("GRANT {} ON TABLE {}.{} TO {}").format(
                sql.SQL(", ").join(map(sql.SQL, sorted(actions))), ident(schema), ident(table), ident(role)))
        for seq in sorted(sequences):
            cursor.execute(sql.SQL("GRANT USAGE ON SEQUENCE {}.{} TO {}").format(ident(schema), ident(seq), ident(role)))
        for table, columns in update_columns.items():
            cursor.execute(sql.SQL("GRANT UPDATE ({}) ON {}.{} TO {}").format(
                sql.SQL(", ").join(map(ident, columns)), ident(schema), ident(table), ident(role)))
    # Compare actual effective rights including PUBLIC, column grants and grant options.
    cursor.execute("SELECT has_schema_privilege(%s,%s,'USAGE'),has_schema_privilege(%s,%s,'USAGE WITH GRANT OPTION')",
                   (role, schema, role, schema))
    if cursor.fetchone() != (True, False):
        raise ValueError("Schema usage differs from the reviewed policy")
    cursor.execute("""
        SELECT c.relname,p,
               has_table_privilege(%s,c.oid,p) AS actual,
               has_table_privilege(%s,c.oid,p||' WITH GRANT OPTION') AS delegable,
               CASE WHEN p IN ('SELECT','INSERT','UPDATE','REFERENCES')
                    THEN has_any_column_privilege(%s,c.oid,p) ELSE false END AS any_column,
               CASE WHEN p IN ('SELECT','INSERT','UPDATE','REFERENCES')
                    THEN has_any_column_privilege(%s,c.oid,p||' WITH GRANT OPTION') ELSE false END AS column_delegable
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        CROSS JOIN unnest(%s::text[]) p
        WHERE n.nspname=%s AND c.relkind IN ('r','p','v','m','f')
    """, (role, role, role, role, sorted(DML | {'REFERENCES', 'TRUNCATE', 'TRIGGER'} |
                                        ({'MAINTAIN'} if cursor.connection.server_version >= 170000 else set())), schema))
    for table, privilege, actual, delegable, column, column_delegable in cursor:
        expected = privilege in manifest.get(table, ())
        column_update = privilege == 'UPDATE' and table in update_columns
        if actual != expected or delegable or column_delegable or (column and not (expected or column_update)):
            raise ValueError(f"Unreviewed or missing table privilege: {table} {privilege}")
    for table, columns in update_columns.items():
        cursor.execute("""
            SELECT attname FROM pg_attribute WHERE attrelid=%s AND attnum>0 AND NOT attisdropped
            AND has_column_privilege(%s,attrelid,attnum,'UPDATE')
        """, (objects[table][0], role))
        if {row[0] for row in cursor} != set(columns):
            raise ValueError(f"UPDATE privileges differ from the reviewed columns for {table}")
    cursor.execute("""
        SELECT c.relname,p,has_sequence_privilege(%s,c.oid,p),
               has_sequence_privilege(%s,c.oid,p||' WITH GRANT OPTION')
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        CROSS JOIN unnest(ARRAY['USAGE','UPDATE','SELECT']) p WHERE n.nspname=%s AND c.relkind='S'
    """, (role, role, schema))
    for name, privilege, actual, delegable in cursor:
        if actual != (name in sequences and privilege == 'USAGE') or delegable:
            raise ValueError(f"Unreviewed or missing sequence privilege: {name} {privilege}")
    check_principal(cursor, schema, role)
    return len(manifest), len(sequences)


def check_ambient(cursor, schema, role):
    """Reject accessible non-system relations outside the reviewed namespace."""
    cursor.execute("""
        SELECT EXISTS (
            SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname<>%s AND n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
              AND has_schema_privilege(%s,n.oid,'USAGE') AND (
                (c.relkind IN ('r','p','m','v','f') AND
                 (has_table_privilege(%s,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                  OR has_any_column_privilege(%s,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
                OR (c.relkind='S' AND has_sequence_privilege(%s,c.oid,'SELECT,USAGE,UPDATE'))))
    """, (schema, role, role, role, role))
    if cursor.fetchone()[0]:
        raise ValueError("Unreviewed access in another application schema")
    cursor.execute("""
        SELECT EXISTS (SELECT FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a
            WHERE d.defaclobjtype IN ('r','S','n') AND a.grantee IN
            (0,(SELECT oid FROM pg_roles WHERE rolname=%s))
            AND (d.defaclnamespace=0 OR d.defaclnamespace=%s::regnamespace))
    """, (role, sql.Identifier(schema).as_string(cursor.connection)))
    if cursor.fetchone()[0]:
        raise ValueError("Default privileges open future objects before explicit review")
    # An invoker routine is still callable via PUBLIC unless its ACL says
    # otherwise. Refuse unexpected routines even if their owner is not web.
    expected = json.loads(FUNCTION_MANIFEST.read_text(encoding="utf-8"))
    cursor.execute("""
        SELECT n.nspname,p.proname,oidvectortypes(p.proargtypes),p.prorettype::regtype::text,
               p.prosecdef,has_function_privilege(%s,p.oid,'EXECUTE WITH GRANT OPTION')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
          AND has_schema_privilege(%s,n.oid,'USAGE') AND has_function_privilege(%s,p.oid,'EXECUTE')
    """, (role, role, role))
    seen = set()
    for namespace, name, args, returns, definer, delegable in cursor:
        signature = f"{name}({args})"
        if namespace != schema or expected.get(signature) != returns or definer or delegable:
            raise ValueError(f"Unreviewed executable routine: {namespace}.{signature}")
        seen.add(signature)
    # Trigger functions are invoked by installed triggers; direct EXECUTE is not
    # required for firing. Helpers used by those invoker routines must resolve.
    if any(name not in seen for name, returns in expected.items() if returns != 'trigger'):
        raise ValueError("Missing required executable application helper")


def verify_release(cursor, schema, revision, record=False):
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError("A full Git commit SHA is required")
    relation = sql.Identifier(schema, RECEIPT)
    if record:
        cursor.execute(sql.SQL("CREATE TABLE IF NOT EXISTS {} (revision varchar(40) PRIMARY KEY, "
                               "verified_at timestamptz NOT NULL DEFAULT now())").format(relation))
        cursor.execute(sql.SQL("INSERT INTO {} (revision) VALUES (%s) ON CONFLICT DO NOTHING").format(relation),
                       (revision,))
    else:
        cursor.execute(sql.SQL("SELECT EXISTS (SELECT FROM {} WHERE revision=%s)").format(relation), (revision,))
        if not cursor.fetchone()[0]:
            raise ValueError("This release has no off-service migration receipt")


def verify_hosted_release():
    if any(os.getenv(key) for key in ('BOOTSTRAP_ADMIN_PASSWORD', 'MIGRATION_DATABASE_URL',
                                      'DEPLOYMENT_DATABASE_URL')):
        raise ValueError("Remove privileged credentials from the Render service")
    main(['--database', os.environ['NEOAPPS_RUNTIME_DATABASE'],
          '--schema', os.environ['NEOAPPS_RUNTIME_SCHEMA'],
          '--runtime', os.environ['NEOAPPS_RUNTIME_ROLE'],
          '--release', os.environ['RENDER_GIT_COMMIT']])


def prepare_release(conn, args, revision):
    if not args.migrator or not args.standby or len({args.migrator, args.runtime, args.standby}) != 3:
        raise ValueError("Specify separate migration, runtime and standby logins")
    if not re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', args.schema):
        raise ValueError("Unsupported application schema for bootstrap")
    root = Path(__file__).resolve().parents[1]
    head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"], text=True)
    if head != revision or dirty.strip():
        raise ValueError("Apply requires the reviewed SHA and a clean application checkout")
    with conn, conn.cursor() as cursor:
        cursor.execute("""SELECT current_database(),current_user,session_user,current_schema(),
            NOT (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls),
            has_schema_privilege(current_user,%s,'CREATE'),
            NOT EXISTS (SELECT FROM pg_auth_members WHERE member=r.oid),
            NOT has_database_privilege(current_user,current_database(),'CREATE')
            FROM pg_roles r WHERE rolname=current_user""", (args.schema,))
        expected = (args.database, args.migrator, args.migrator, args.schema, True, True, True, True)
        if cursor.fetchone() != expected:
            raise ValueError("Unexpected target database or grantor")
        for role in (args.runtime, args.standby):
            check_principal(cursor, args.schema, role)
        # This session stays alive while bootstrap uses its own connections.
        cursor.execute("SELECT pg_try_advisory_lock(hashtext(current_database()),hashtext(%s))",
                       ('neoapps-release-' + args.schema,))
        if not cursor.fetchone()[0]:
            raise ValueError("Another database release is in progress")
    subprocess.run([sys.executable, str(root / 'scripts' / 'bootstrap_database.py')], cwd=root,
                   env=dict(os.environ, DATABASE_BOOTSTRAP_EXPECTED_SCHEMA=args.schema), check=True)
    with conn, conn.cursor() as cursor:
        cursor.execute("SET LOCAL search_path TO pg_catalog")
        cursor.execute("SET LOCAL lock_timeout='5s'")
        cursor.execute("SET LOCAL statement_timeout='30s'")
        verify_release(cursor, args.schema, revision, record=True)
        for role in (args.runtime, args.standby):
            configure(cursor, args.schema, role, apply=True)
            check_ambient(cursor, args.schema, role)
    print("Bootstrap completed and both runtime policies verified for", revision)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--schema', required=True)
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--apply', action='store_true', help='Prepare an off-service release and both runtime policies')
    parser.add_argument('--migrator', help='Expected off-service migration login in apply mode')
    parser.add_argument('--standby', help='Independently held restricted fallback login in apply mode')
    parser.add_argument('--release', help='Reviewed Git SHA (defaults to RENDER_GIT_COMMIT in verify mode)')
    args = parser.parse_args(argv)
    if args.apply and (os.getenv('RENDER') or os.getenv('RENDER_SERVICE_ID')):
        raise ValueError("Apply release privileges only from the off-service deployment environment")
    conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=5)
    try:
        if args.apply:
            prepare_release(conn, args, args.release)
            return
        conn.set_session(readonly=True)
        with conn, conn.cursor() as cursor:
            cursor.execute("SELECT current_schema()")
            if cursor.fetchone() != (args.schema,):
                raise ValueError("Unexpected runtime search path")
            cursor.execute("SET LOCAL search_path TO pg_catalog")
            cursor.execute("SET LOCAL lock_timeout='5s'")
            cursor.execute("SET LOCAL statement_timeout='30s'")
            cursor.execute("SELECT current_database(),current_user,session_user")
            database, actor, login = cursor.fetchone()
            if database != args.database or login != actor or actor != args.runtime:
                raise ValueError("Unexpected target database or grantor")
            revision = args.release or os.getenv("RENDER_GIT_COMMIT")
            if not revision:
                raise ValueError("Missing release revision")
            tables, sequences = configure(cursor, args.schema, args.runtime)
            check_ambient(cursor, args.schema, args.runtime)
            verify_release(cursor, args.schema, revision)
        print(f"Verified {tables} table policies and {sequences} insert sequences")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
