"""DBA-side, database-scoped TEMP preparation. Default is a read-only check."""
import argparse
import os

import psycopg2
from psycopg2 import sql


def prepare(cursor, database, keep, apply=False):
    cursor.execute("SELECT current_database(),datdba,pg_get_userbyid(datdba) "
                   "FROM pg_database WHERE datname=current_database()")
    actual, _, owner = cursor.fetchone()
    if actual != database or database in ('template0', 'template1', 'postgres'):
        raise ValueError("Expected the explicitly named application database")
    for name in keep:
        cursor.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname=%s", (name,))
        if cursor.fetchone() != (True,):
            raise ValueError("An approved temporary-object consumer is missing")
    cursor.execute("""SELECT EXISTS (SELECT FROM pg_database d CROSS JOIN LATERAL
        aclexplode(coalesce(d.datacl,acldefault('d',d.datdba))) a
        WHERE d.datname=current_database() AND a.grantee=0 AND a.privilege_type='TEMPORARY')""")
    public_temp = cursor.fetchone()[0]
    if not apply:
        return public_temp
    cursor.execute("SELECT current_user,session_user")
    if cursor.fetchone() != (owner, owner):
        raise ValueError("Use the database owner's separately controlled session")
    for name in keep:
        cursor.execute(sql.SQL("GRANT TEMPORARY ON DATABASE {} TO {}").format(
            sql.Identifier(database), sql.Identifier(name)))
    cursor.execute(sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
    return public_temp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--keep-temp', nargs='*', help='DBA-reviewed complete list of legitimate exceptions')
    args = parser.parse_args()
    if args.apply and args.keep_temp is None:
        parser.error('Explicitly supply the reviewed exception list, including an empty list')
    with psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=5) as conn:
        conn.set_session(readonly=not args.apply)
        with conn.cursor() as cursor:
            cursor.execute('SET LOCAL search_path TO pg_catalog')
            cursor.execute("SET LOCAL lock_timeout='3s'")
            prior = prepare(cursor, args.database, args.keep_temp or (), args.apply)
            print('PUBLIC TEMP before change:', prior)


if __name__ == '__main__':
    main()
