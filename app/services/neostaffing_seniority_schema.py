"""Explicit, idempotent seniority nullability upgrade. Never backfill employee dates."""
from sqlalchemy import inspect, text

from app.extensions import db
from app.models import StaffingPerson

CONSTRAINT = 'ck_staffing_people_seniority_required'


def sync_seniority_schema():
    if db.engine.dialect.name == 'sqlite':
        if db.session().in_transaction():
            needed = _sqlite_needs_upgrade(db.session.connection())
        else:
            with db.engine.connect() as probe:
                needed = _sqlite_needs_upgrade(probe)
        if not needed:
            return
        if db.session.new or db.session.dirty or db.session.deleted or db.session().in_transaction():
            raise RuntimeError('Run the Staffing seniority schema upgrade before opening an employee transaction.')
        _upgrade_sqlite()
        return
    connection = db.session.connection()
    inspector = inspect(connection)
    if not inspector.has_table('staffing_people'):
        return
    from app.services.neostaffing_classification_schema import _ensure_classification_constraint
    connection.execute(text("SET LOCAL lock_timeout='5s'"))
    connection.execute(text('SELECT pg_advisory_xact_lock(7483327341909)'))
    _ensure_classification_constraint(connection, 'staffing_people')
    nullable = next(c['nullable'] for c in inspector.get_columns('staffing_people') if c['name']=='seniority_date')
    checks = {c['name'] for c in inspector.get_check_constraints('staffing_people')}
    if nullable and CONSTRAINT in checks:
        return
    if connection.dialect.name == 'postgresql':
        connection.execute(text("SET LOCAL lock_timeout='5s'"))
        connection.execute(text('SELECT pg_advisory_xact_lock(61090401)'))
        checks = {c['name'] for c in inspect(connection).get_check_constraints('staffing_people')}
        if CONSTRAINT not in checks:
            connection.execute(text(f"ALTER TABLE staffing_people ADD CONSTRAINT {CONSTRAINT} "
                "CHECK (seniority_date IS NOT NULL OR classification = 'seasonal') NOT VALID"))
        connection.execute(text('ALTER TABLE staffing_people ALTER COLUMN seniority_date DROP NOT NULL'))
        connection.execute(text(f'ALTER TABLE staffing_people VALIDATE CONSTRAINT {CONSTRAINT}'))


def _sqlite_needs_upgrade(connection):
    inspector = inspect(connection)
    if not inspector.has_table('staffing_people'):
        return False
    nullable = next(c['nullable'] for c in inspector.get_columns('staffing_people') if c['name']=='seniority_date')
    checks = inspector.get_check_constraints('staffing_people')
    return (not nullable or CONSTRAINT not in {c['name'] for c in checks}
            or not any(c['name']=='ck_staffing_people_classification' and "'pre_seniority'" in c['sqltext'] for c in checks))


def _upgrade_sqlite():
    table = StaffingPerson.__table__
    legacy = 'staffing_people_seniority_upgrade'
    with db.engine.connect() as connection:
        if inspect(connection).has_table(legacy):
            raise RuntimeError('Unfinished Staffing seniority upgrade; no records were changed.')
        columns = [c['name'] for c in inspect(connection).get_columns(table.name)]
        owned_sql = connection.execute(text("SELECT type,name,sql FROM sqlite_master "
            "WHERE tbl_name='staffing_people' AND type IN ('index','trigger') AND sql IS NOT NULL")).all()
        if set(columns) - set(table.columns.keys()):
            raise RuntimeError('Unknown Staffing columns; preserve them before upgrading seniority.')
        foreign_keys = connection.exec_driver_sql('PRAGMA foreign_keys').scalar()
        connection.commit()
        connection.exec_driver_sql('PRAGMA foreign_keys=OFF')
        connection.exec_driver_sql('PRAGMA legacy_alter_table=ON')
        connection.commit()
        try:
            connection.exec_driver_sql('BEGIN IMMEDIATE')
            connection.exec_driver_sql(f'ALTER TABLE staffing_people RENAME TO {legacy}')
            for index in inspect(connection).get_indexes(legacy):
                name = index['name'].replace('"','""')
                connection.exec_driver_sql(f'DROP INDEX "{name}"')
            table.create(connection)
            quoted = ','.join('"'+name.replace('"','""')+'"' for name in columns)
            connection.exec_driver_sql(f'INSERT INTO staffing_people ({quoted}) SELECT {quoted} FROM {legacy}')
            connection.exec_driver_sql(f'DROP TABLE {legacy}')
            existing = set(connection.execute(text("SELECT name FROM sqlite_master WHERE type IN ('index','trigger')")).scalars())
            for _kind, name, sql in owned_sql:
                if name not in existing:
                    connection.exec_driver_sql(sql)
            if connection.exec_driver_sql('PRAGMA foreign_key_check').first():
                raise RuntimeError('Staffing seniority upgrade failed foreign-key validation.')
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.exec_driver_sql('PRAGMA legacy_alter_table=OFF')
            connection.exec_driver_sql(f'PRAGMA foreign_keys={int(bool(foreign_keys))}')
            connection.commit()
