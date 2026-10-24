"""Additive, repeatable guards for permanent source evidence and import audit."""
from sqlalchemy import inspect,text


def install(connection):
    if not inspect(connection).has_table('frost_document_audits'): return
    if connection.dialect.name=='postgresql':
        connection.execute(text("""CREATE OR REPLACE FUNCTION frost_audit_immutable() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'FROST import audit is append-only'; END; $$ LANGUAGE plpgsql"""))
        connection.execute(text('DROP TRIGGER IF EXISTS frost_audit_immutable ON frost_document_audits'))
        connection.execute(text('CREATE TRIGGER frost_audit_immutable BEFORE UPDATE OR DELETE ON frost_document_audits FOR EACH ROW EXECUTE FUNCTION frost_audit_immutable()'))
    elif connection.dialect.name=='sqlite':
        for action in ('UPDATE','DELETE'):
            connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS frost_audit_no_{action.lower()} BEFORE {action} ON frost_document_audits BEGIN SELECT RAISE(ABORT,'FROST import audit is append-only'); END"))
    else: raise RuntimeError('Unsupported FROST schema dialect.')
    verify(connection)


def verify(connection):
    if connection.dialect.name=='postgresql':
        guarded=connection.execute(text("SELECT count(*) FROM pg_trigger WHERE tgrelid='frost_document_audits'::regclass AND tgname='frost_audit_immutable' AND NOT tgisinternal AND tgenabled <> 'D'")).scalar()==1
    else:
        guarded=connection.execute(text("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name IN ('frost_audit_no_update','frost_audit_no_delete')")).scalar()==2
    if not guarded: raise RuntimeError('FROST append-only import audit protection is missing.')
