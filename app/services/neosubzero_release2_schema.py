"""Nondestructive constraint relaxation; no operational backfill or cleanup."""
import re
from sqlalchemy import inspect, text
from app.extensions import db

NULLABLE = ('departure_deice_event_id','gallons','surface_area','started_at_utc','ended_at_utc')
EVENTS = 'neosubzero_departure_deice_events'
SPRAYS = 'neosubzero_spray_records'
PARENT_CHECK='ck_neosubzero_spray_one_parent'
PARENT_RULE='(departure_deice_event_id IS NOT NULL AND pretreat_state_id IS NULL) OR (departure_deice_event_id IS NULL AND pretreat_state_id IS NOT NULL)'


def _legacy_unique(connection):
    return [item for item in inspect(connection).get_unique_constraints(EVENTS)
            if item['column_names'] == ['sort_date_mission_id']]


def verify_release2_contract(connection):
    if _legacy_unique(connection):
        raise RuntimeError('NeoSubZero legacy mission-only event uniqueness remains.')
    columns={item['name']:item for item in inspect(connection).get_columns(SPRAYS)}
    if any(not columns[name]['nullable'] for name in NULLABLE):
        raise RuntimeError('NeoSubZero participation still requires fake gallons/timestamps.')
    if not any(item['name']==PARENT_CHECK for item in inspect(connection).get_check_constraints(SPRAYS)):
        raise RuntimeError('NeoSubZero participation parent identity protection is missing.')
    indexes=inspect(connection).get_indexes(SPRAYS)+inspect(connection).get_unique_constraints(SPRAYS)
    if not any(item.get('column_names')==['pretreat_state_id','pass_number','position_number'] and item.get('unique',True) for item in indexes):
        raise RuntimeError('NeoSubZero Pretreat participation uniqueness is missing.')
    if connection.dialect.name=='postgresql':
        guarded=connection.execute(text("SELECT count(*) FROM pg_trigger WHERE tgrelid='neosubzero_audit_entries'::regclass AND tgname='neosubzero_audit_immutable' AND NOT tgisinternal AND tgenabled <> 'D'")).scalar()
    else:
        guarded=connection.execute(text("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name IN ('neosubzero_audit_no_update','neosubzero_audit_no_delete')")).scalar()==2
    if not guarded:
        raise RuntimeError('NeoSubZero append-only audit protection is missing.')


def sync_release2_schema():
    from app.models import NeoSubZeroOperationContext, NeoSubZeroAuditEntry
    connection=db.session.connection()
    inspector=inspect(connection)
    if not inspector.has_table(EVENTS) or not inspector.has_table(SPRAYS):
        return
    if connection.dialect.name == 'postgresql':
        connection.execute(text('SELECT pg_advisory_xact_lock(7483327341930)'))
    for model in (NeoSubZeroOperationContext,NeoSubZeroAuditEntry):
        model.__table__.create(connection,checkfirst=True)
    if connection.dialect.name == 'postgresql':
        for constraint in _legacy_unique(connection):
            # Names come only from this known legacy contract, never user input.
            if constraint['name'] != 'uq_neosubzero_departure_deice_mission':
                raise RuntimeError('Unrecognized SubZero mission constraint; schema review required.')
            connection.execute(text(f'ALTER TABLE {EVENTS} DROP CONSTRAINT uq_neosubzero_departure_deice_mission'))
        columns={item['name']:item for item in inspect(connection).get_columns(SPRAYS)}
        for name in NULLABLE:
            if not columns[name]['nullable']:
                connection.execute(text(f'ALTER TABLE {SPRAYS} ALTER COLUMN {name} DROP NOT NULL'))
        if not any(item['name']==PARENT_CHECK for item in inspect(connection).get_check_constraints(SPRAYS)):
            connection.execute(text(f'ALTER TABLE {SPRAYS} ADD CONSTRAINT {PARENT_CHECK} CHECK ({PARENT_RULE})'))
        connection.execute(text(f'CREATE UNIQUE INDEX IF NOT EXISTS uq_neosubzero_spray_pretreat_pass_position ON {SPRAYS}(pretreat_state_id,pass_number,position_number)'))
        connection.execute(text('''CREATE OR REPLACE FUNCTION neosubzero_audit_immutable() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'SubZero audit evidence is append-only'; END; $$ LANGUAGE plpgsql'''))
        connection.execute(text('DROP TRIGGER IF EXISTS neosubzero_audit_immutable ON neosubzero_audit_entries'))
        connection.execute(text('CREATE TRIGGER neosubzero_audit_immutable BEFORE UPDATE OR DELETE ON neosubzero_audit_entries FOR EACH ROW EXECUTE FUNCTION neosubzero_audit_immutable()'))
    elif connection.dialect.name == 'sqlite':
        rebuild=[]
        if _legacy_unique(connection): rebuild.append(EVENTS)
        columns={item['name']:item for item in inspect(connection).get_columns(SPRAYS)}
        if any(not columns[name]['nullable'] for name in NULLABLE) or not any(item['name']==PARENT_CHECK for item in inspect(connection).get_check_constraints(SPRAYS)): rebuild.append(SPRAYS)
        if rebuild:
            db.session.commit()
            _sqlite_rebuild(rebuild)
            connection=db.session.connection()
        connection.execute(text(f'CREATE UNIQUE INDEX IF NOT EXISTS uq_neosubzero_spray_pretreat_pass_position ON {SPRAYS}(pretreat_state_id,pass_number,position_number)'))
        for action in ('UPDATE','DELETE'):
            connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS neosubzero_audit_no_{action.lower()} BEFORE {action} ON neosubzero_audit_entries BEGIN SELECT RAISE(ABORT,'SubZero audit evidence is append-only'); END"))
    else:
        raise RuntimeError('Unsupported SubZero schema dialect.')
    from app.services.frost_schema import install
    install(connection)
    verify_release2_contract(connection)


def _sqlite_rebuild(tables):
    # SQLite requires a transactional copy to relax constraints. Preserve exact
    # values/IDs, indexes, triggers, and FK references; never synthesize evidence.
    with db.engine.connect() as connection:
        enabled=connection.exec_driver_sql('PRAGMA foreign_keys').scalar()
        connection.commit();connection.exec_driver_sql('PRAGMA foreign_keys=OFF');connection.commit()
        try:
            connection.exec_driver_sql('BEGIN IMMEDIATE')
            for table in tables:
                ddl=connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(table,)).scalar_one()
                objects=connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL",(table,)).scalars().all()
                if table == EVENTS:
                    ddl,count=re.subn(r'CONSTRAINT\s+uq_neosubzero_departure_deice_mission\s+UNIQUE\s*\(\s*sort_date_mission_id\s*\)\s*,?', '',ddl,flags=re.I)
                    if count != 1: raise RuntimeError('Unrecognized SubZero event DDL; no migration applied.')
                    ddl=re.sub(r',\s*\)\s*$', '\n)', ddl)
                else:
                    for column in NULLABLE:
                        ddl=re.sub(r'(\b'+column+r'\s+\w+(?:\([^)]*\))?)\s+NOT NULL',r'\1',ddl,flags=re.I)
                    if PARENT_CHECK not in ddl:
                        ddl=re.sub(r'\)\s*$', f', CONSTRAINT {PARENT_CHECK} CHECK ({PARENT_RULE})\n)', ddl)
                temporary=table+'_release2_upgrade'
                if inspect(connection).has_table(temporary): raise RuntimeError('Unfinished SubZero upgrade requires review.')
                ddl=re.sub(r'(CREATE TABLE\s+)["`\[]?'+table+r'["`\]]?',r'\1'+temporary,ddl,count=1,flags=re.I)
                connection.exec_driver_sql(ddl)
                columns=','.join('"'+column['name']+'"' for column in inspect(connection).get_columns(table))
                connection.exec_driver_sql(f'INSERT INTO {temporary} ({columns}) SELECT {columns} FROM {table}')
                connection.exec_driver_sql(f'DROP TABLE {table}')
                connection.exec_driver_sql(f'ALTER TABLE {temporary} RENAME TO {table}')
                for statement in objects: connection.exec_driver_sql(statement)
            if connection.exec_driver_sql('PRAGMA foreign_key_check').first(): raise RuntimeError('SubZero upgrade failed FK validation.')
            connection.commit()
        except Exception:
            connection.rollback();raise
        finally:
            connection.exec_driver_sql(f'PRAGMA foreign_keys={int(enabled)}');connection.commit()
