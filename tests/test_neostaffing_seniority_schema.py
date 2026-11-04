"""Legacy SQLite records/FKs/indexes survive the optional Seasonal-date upgrade."""
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from sqlalchemy import MetaData, inspect, text
from sqlalchemy.exc import IntegrityError
from app import create_app
from app.extensions import db
from app.models import StaffingPerson
from app.services.neostaffing_seniority_schema import sync_seniority_schema, CONSTRAINT


class SenioritySchemaTest(unittest.TestCase):
    def setUp(self):
        self.directory=TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        app=create_app(type('Config',(),dict(TESTING=True,SECRET_KEY='seniority-test',
            SQLALCHEMY_DATABASE_URI='sqlite:///'+str(Path(self.directory.name)/'legacy.db'),
            SQLALCHEMY_TRACK_MODIFICATIONS=False,AUTO_BOOTSTRAP_DATABASE=False)))
        self.context=app.app_context();self.context.push();self.addCleanup(self.context.pop)
        def close_database():
            db.session.remove();db.engine.dispose()
        self.addCleanup(close_database)
        old=StaffingPerson.__table__.to_metadata(MetaData())
        old.c.seniority_date.nullable=False
        old.constraints.remove(next(c for c in old.constraints if c.name==CONSTRAINT))
        old.create(db.engine)
        with db.engine.begin() as c:
            c.exec_driver_sql('PRAGMA foreign_keys=ON')
            c.execute(old.insert().values(id=7,employee_id='KEEP',first_name='Same',last_name='Employee',
                seniority_date=date(2020,1,2),classification='part_time',employee_status='active'))
            c.exec_driver_sql('CREATE TABLE retained_child(id INTEGER PRIMARY KEY, person_id INTEGER REFERENCES staffing_people(id) ON DELETE CASCADE)')
            c.exec_driver_sql('INSERT INTO retained_child VALUES(11,7)')
            c.exec_driver_sql('CREATE UNIQUE INDEX normalized_employee_test ON staffing_people(lower(trim(employee_id)))')
            c.exec_driver_sql('CREATE TABLE seniority_audit_test(id INTEGER)')
            c.exec_driver_sql('CREATE TRIGGER keep_employee_trigger AFTER UPDATE ON staffing_people BEGIN INSERT INTO seniority_audit_test VALUES(NEW.id); END')

    def assert_preserved(self):
        with db.engine.connect() as c:
            self.assertEqual(c.exec_driver_sql('PRAGMA foreign_keys').scalar(),1)
            self.assertEqual(c.exec_driver_sql('SELECT id,employee_id,seniority_date FROM staffing_people').all(),[(7,'KEEP','2020-01-02')])
            self.assertEqual(c.exec_driver_sql('SELECT * FROM retained_child').all(),[(11,7)])
            self.assertEqual(c.exec_driver_sql('PRAGMA foreign_key_check').all(),[])
            names=set(c.execute(text("SELECT name FROM sqlite_master WHERE type IN ('trigger','index')")).scalars())
            self.assertIn('normalized_employee_test',names);self.assertIn('keep_employee_trigger',names)
            self.assertEqual(c.exec_driver_sql('SELECT * FROM seniority_audit_test').all(),[])

    def test_upgrade_is_idempotent_preserves_rows_children_and_custom_guards(self):
        sync_seniority_schema();self.assert_preserved();sync_seniority_schema();self.assert_preserved()
        self.assertTrue(next(c['nullable'] for c in inspect(db.engine).get_columns('staffing_people') if c['name']=='seniority_date'))
        with db.engine.begin() as c:
            c.execute(StaffingPerson.__table__.insert().values(employee_id='SEASONAL',first_name='No',last_name='Date',classification='seasonal'))
        with self.assertRaises(IntegrityError), db.engine.begin() as c:
            c.execute(StaffingPerson.__table__.insert().values(employee_id='REGULAR',first_name='Must',last_name='HaveDate',classification='part_time'))

    def test_ddl_failure_rolls_back_and_preserves_original_table(self):
        with patch.object(StaffingPerson.__table__,'create',side_effect=RuntimeError('Synthetic DDL failure')):
            with self.assertRaisesRegex(RuntimeError,'Synthetic'):sync_seniority_schema()
        self.assert_preserved()
        self.assertFalse(next(c['nullable'] for c in inspect(db.engine).get_columns('staffing_people') if c['name']=='seniority_date'))

    def test_upgrade_does_not_commit_or_rollback_a_pending_employee_edit(self):
        person=db.session.get(StaffingPerson,7);person.first_name='Pending'
        with self.assertRaisesRegex(RuntimeError,'before opening'):sync_seniority_schema()
        self.assertEqual(person.first_name,'Pending')
        self.assertIn(person,db.session.dirty)
        db.session.rollback();self.assert_preserved()
