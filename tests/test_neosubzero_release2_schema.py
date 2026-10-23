"""Populated Release 1 upgrades: exact row preservation, repeatable constraints."""
import unittest
from sqlalchemy import MetaData, UniqueConstraint, text, inspect
from sqlalchemy.schema import CreateTable
from app.extensions import db
from app.models import NeoSubZeroSprayRecord, NeoSubZeroDepartureDeiceEvent, NeoSubZeroAuditEntry
from app.services.neosubzero_schema import NEOSUBZERO_OPTIONAL_COLUMNS
from app.services.neosubzero_release2_schema import sync_release2_schema, verify_release2_contract, NULLABLE
from app.services.schema_sync import sync_database_schema
from tests import test_neosubzero_ucc as fixtures

NEW_COLUMNS={
    'neosubzero_departure_deice_events':('flight_number_snapshot','cleared_at_utc','clear_outcome','pass_evidence'),
    'neosubzero_pretreat_states':('original_mission_id','flight_number_snapshot','pass_evidence'),
    'neosubzero_spray_records':('pretreat_state_id','tail_number_snapshot','flight_number_snapshot','timezone_snapshot','ramp_snapshot','parking_snapshot','official_at_utc','participation_selected','crew_override','excluded_at_utc'),
}


def legacy_rows(f):
    from datetime import datetime
    event=f._deice_event(f.alpha,'finished')
    record=NeoSubZeroSprayRecord(sort_date_operation_id=f.operation.id,sort_date_mission_id=f.alpha.id,
        departure_deice_event_id=event.id,pass_number=1,position_number=1,gallons=12,
        truck_number_snapshot='OLD12',pass_type='type_i',fluid_name_snapshot='ACTUAL OLD PRODUCT',
        concentration_percent_snapshot=60,surface_area='wings_only',started_at_utc=datetime(2026,9,1,3),
        ended_at_utc=datetime(2026,9,1,3,5),driver_name_snapshot='Original Driver')
    db.session.add(record);db.session.commit()
    return event.id,record.id,record.created_at,event.updated_at


def make_sqlite_legacy():
    metadata=MetaData()
    for table in db.metadata.sorted_tables: table.to_metadata(metadata)
    for name,columns in NEW_COLUMNS.items():
        table=metadata.tables[name]
        for constraint in list(table.constraints):
            if any(column.name in columns for column in constraint.columns) or constraint.name=='ck_neosubzero_spray_one_parent': table.constraints.remove(constraint)
        for index in list(table.indexes):
            if any(column.name in columns for column in index.columns): table.indexes.remove(index)
        for column in columns: table._columns.remove(table.c[column])
        if name.endswith('departure_deice_events'): table.append_constraint(UniqueConstraint('sort_date_mission_id',name='uq_neosubzero_departure_deice_mission'))
        if name.endswith('spray_records'):
            for column in NULLABLE: table.c[column].nullable=False
    db.session.remove()
    with db.engine.connect() as connection:
        connection.exec_driver_sql('PRAGMA foreign_keys=OFF');connection.commit()
        connection.exec_driver_sql('BEGIN IMMEDIATE')
        for name in ('neosubzero_audit_entries','neosubzero_operation_contexts'): connection.exec_driver_sql('DROP TABLE '+name)
        for name in NEW_COLUMNS:
            table=metadata.tables[name];columns=','.join('"'+c.name+'"' for c in table.columns)
            ddl=str(CreateTable(table).compile(db.engine)).replace('CREATE TABLE '+name,'CREATE TABLE '+name+'_old',1)
            connection.exec_driver_sql(ddl)
            connection.exec_driver_sql(f'INSERT INTO {name}_old ({columns}) SELECT {columns} FROM {name}')
            connection.exec_driver_sql('DROP TABLE '+name)
            connection.exec_driver_sql(f'ALTER TABLE {name}_old RENAME TO {name}')
        connection.commit()
        connection.exec_driver_sql('PRAGMA foreign_keys=ON');connection.commit()


class Release2SqliteSchemaTest(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.NeoSubZeroUccTest();self.f.setUp();self.addCleanup(self.f.tearDown)

    def test_populated_release1_upgrade_idempotence_and_immutable_audit(self):
        original=legacy_rows(self.f)
        operation_id=self.f.operation.id
        make_sqlite_legacy()
        with db.engine.connect() as connection:
            with self.assertRaises(RuntimeError): verify_release2_contract(connection)
        # Use the same canonical entry point as bootstrap, not an ad hoc migration.
        sync_database_schema(self.f.app);sync_database_schema(self.f.app)
        verify_release2_contract(db.session.connection())
        event=db.session.get(NeoSubZeroDepartureDeiceEvent,original[0]);record=db.session.get(NeoSubZeroSprayRecord,original[1])
        self.assertEqual((event.id,record.id,record.created_at,event.updated_at),original)
        self.assertEqual(record.driver_name_snapshot,'Original Driver')
        self.assertEqual(record.fluid_name_snapshot,'ACTUAL OLD PRODUCT')
        self.assertIsNone(record.official_at_utc);self.assertIsNone(record.tail_number_snapshot)
        self.assertEqual(record.gallons,12)
        self.assertFalse(db.session.execute(text('PRAGMA foreign_key_check')).all())
        for table,columns in NEOSUBZERO_OPTIONAL_COLUMNS.items():
            self.assertFalse(set(columns)-{c['name'] for c in inspect(db.engine).get_columns(table)})
        audit=NeoSubZeroAuditEntry(sort_date_operation_id=operation_id,action='schema_test')
        db.session.add(audit);db.session.commit();aid=audit.id
        for statement in ('UPDATE neosubzero_audit_entries SET action=\'changed\' WHERE id=:id','DELETE FROM neosubzero_audit_entries WHERE id=:id'):
            with self.assertRaises(Exception): db.session.execute(text(statement),{'id':aid})
            db.session.rollback()
        self.assertEqual(db.session.get(NeoSubZeroAuditEntry,aid).action,'schema_test')
