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
    for table in ('frost_model_artifacts', 'frost_model_releases'):
        if not inspect(connection).has_table(table): continue
        if connection.dialect.name=='postgresql':
            connection.execute(text(f'DROP TRIGGER IF EXISTS frost_model_immutable ON {table}'))
            connection.execute(text(f'CREATE TRIGGER frost_model_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION frost_audit_immutable()'))
        else:
            for action in ('UPDATE','DELETE'):
                connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'FROST model history is append-only'); END"))
    if inspect(connection).has_table('frost_history_tasks'):
        if connection.dialect.name=='postgresql':
            connection.execute(text("""CREATE OR REPLACE FUNCTION frost_research_receipt_immutable() RETURNS trigger AS $$
                BEGIN IF OLD.kind IN ('research_model','auto_capture','xchange_page') AND OLD.status='ready'
                THEN RAISE EXCEPTION 'Verified FROST research evidence is immutable'; END IF;
                IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW; END; $$ LANGUAGE plpgsql"""))
            connection.execute(text('DROP TRIGGER IF EXISTS frost_research_receipt_immutable ON frost_history_tasks'))
            connection.execute(text('CREATE TRIGGER frost_research_receipt_immutable BEFORE UPDATE OR DELETE ON frost_history_tasks FOR EACH ROW EXECUTE FUNCTION frost_research_receipt_immutable()'))
        else:
            for action in ('UPDATE','DELETE'):
                connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS frost_research_receipt_no_{action.lower()} BEFORE {action} ON frost_history_tasks WHEN OLD.kind IN ('research_model','auto_capture','xchange_page') AND OLD.status='ready' BEGIN SELECT RAISE(ABORT,'Verified FROST research evidence is immutable'); END"))
    verify(connection)


def verify(connection):
    if connection.dialect.name=='postgresql':
        guarded=connection.execute(text("SELECT count(*) FROM pg_trigger WHERE tgrelid='frost_document_audits'::regclass AND tgname='frost_audit_immutable' AND NOT tgisinternal AND tgenabled <> 'D'")).scalar()==1
    else:
        guarded=connection.execute(text("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name IN ('frost_audit_no_update','frost_audit_no_delete')")).scalar()==2
    if not guarded: raise RuntimeError('FROST append-only import audit protection is missing.')
    if inspect(connection).has_table('frost_history_tasks'):
        if connection.dialect.name=='postgresql':
            count=connection.execute(text("SELECT count(*) FROM pg_trigger WHERE tgrelid='frost_history_tasks'::regclass AND tgname='frost_research_receipt_immutable' AND NOT tgisinternal AND tgenabled <> 'D'")).scalar()
            valid=count==1
        else:
            count=connection.execute(text("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name IN ('frost_research_receipt_no_update','frost_research_receipt_no_delete')")).scalar()
            valid=count==2
        if not valid:raise RuntimeError('FROST verified research receipt protection is missing.')
    for table in ('frost_model_artifacts', 'frost_model_releases'):
        if not inspect(connection).has_table(table): raise RuntimeError('FROST model schema missing: '+table)
        if connection.dialect.name=='postgresql':
            count=connection.execute(text(f"SELECT count(*) FROM pg_trigger WHERE tgrelid='{table}'::regclass AND tgname='frost_model_immutable' AND NOT tgisinternal AND tgenabled <> 'D'")).scalar()
            valid=count==1
        else:
            count=connection.execute(text(f"SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name IN ('{table}_no_update','{table}_no_delete')")).scalar()
            valid=count==2
        if not valid: raise RuntimeError('FROST model append-only protection missing: '+table)
