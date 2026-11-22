"""Prepare an owner-reviewed app-object transfer. Run only on the DBA host."""
import argparse
import json
import os
from pathlib import Path

import psycopg2
from psycopg2 import sql


def inventory(cur, schema, prior, target):
    cur.execute("SELECT current_database()")
    database = cur.fetchone()[0]
    cur.execute("""
        SELECT c.relname,c.oid,c.relkind,
            EXISTS (SELECT FROM pg_depend d WHERE d.classid='pg_class'::regclass AND d.objid=c.oid
                    AND d.refclassid='pg_class'::regclass AND d.deptype IN ('i','a')) AS attached
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=%s AND pg_get_userbyid(c.relowner)=%s AND c.relkind IN ('r','p','S','v','m','f')
        AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.classid='pg_class'::regclass AND d.objid=c.oid AND d.deptype='e')
        ORDER BY c.relname
    """, (schema, prior))
    relations = list(cur.fetchall())
    cur.execute("""
        SELECT p.proname,pg_get_function_identity_arguments(p.oid),p.oid,p.prokind FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=%s AND pg_get_userbyid(p.proowner)=%s
        AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e')
        ORDER BY 1,2
    """, (schema, prior))
    routines = list(cur.fetchall())
    if any(kind not in ('r','p','S','v','m') for _,_,kind,_ in relations) or any(k not in ('f','p') for _,_,_,k in routines):
        raise ValueError("Unsupported objects require separate DBA review")
    return {'database': database, 'schema': schema, 'previous': prior, 'target': target,
            'relations': [list(row) for row in relations], 'routines': [list(row) for row in routines]}


def transfer(cur, reviewed):
    schema, prior, target = (reviewed[key] for key in ('schema', 'previous', 'target'))
    # Arguments for overloaded routines come ONLY from the current server catalog,
    # with a catalog-only search path. File contents never become SQL fragments.
    actual = inventory(cur, schema, prior, target)
    if reviewed != actual or not actual['relations'] or target == prior:
        raise ValueError("Owner-reviewed catalog does not match the current database")
    kinds = {'r':'TABLE', 'p':'TABLE', 'v':'VIEW', 'm':'MATERIALIZED VIEW', 'S':'SEQUENCE'}
    for name, _, kind, attached in actual['relations']:
        if kind == 'S' and attached:
            continue  # Postgres moves column-owned sequences with their table.
        cur.execute(sql.SQL("ALTER {} {} OWNER TO {}").format(
            sql.SQL(kinds[kind]), sql.Identifier(schema, name), sql.Identifier(target)))
    for name, args, _, kind in actual['routines']:
        cur.execute(sql.SQL("ALTER {} {}({}) OWNER TO {}").format(sql.SQL('FUNCTION' if kind=='f' else 'PROCEDURE'),
                    sql.Identifier(schema, name), sql.SQL(args), sql.Identifier(target)))
    residual = inventory(cur, schema, prior, target)
    if residual['relations'] or residual['routines']:
        raise ValueError("Transfer incomplete; transaction will roll back")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--previous', required=True)
    parser.add_argument('--target', required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--inventory', type=Path)
    mode.add_argument('--apply-reviewed', type=Path)
    args = parser.parse_args()
    if args.schema.startswith('pg_') or args.schema == 'information_schema':
        raise ValueError("Application schema required")
    conn = psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=5)
    try:
        conn.set_session(readonly=not args.apply_reviewed)
        with conn, conn.cursor() as cur:
            cur.execute("SET LOCAL search_path TO pg_catalog")
            cur.execute("SET LOCAL lock_timeout='3s'")
            cur.execute("SET LOCAL statement_timeout='30s'")
            result = inventory(cur, args.schema, args.previous, args.target)
            if result['database'] != args.database:
                raise ValueError("Unexpected database")
            if args.inventory:
                with args.inventory.open('x', encoding='utf8') as output:
                    json.dump(result, output, indent=2)
            else:
                reviewed = json.loads(args.apply_reviewed.read_text(encoding='utf8'))
                if reviewed != result:
                    raise ValueError("Owner-reviewed catalog is stale or identifies a different scope")
                transfer(cur, reviewed)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
