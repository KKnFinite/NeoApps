"""Personnel handoff serialization and non-destructive schema upgrade on loopback PG."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from sqlalchemy import inspect, text

from app.extensions import db
from app.models import StaffingPerson, StaffingWorkAssignment, User
from app.services import neostaffing as staffing
from app.services import neostaffing_transfers as transfers
from app.services.neostaffing_seniority_schema import sync_seniority_schema, CONSTRAINT
from tests import test_neostaffing_transfers as contracts
from tests import test_neostaffing_routes as fixture
from tests import test_motherbrain_block_out_authority_postgres as pg


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class EmployeeTransferPostgresTest(contracts.EmployeeTransferTest):
    def setUp(self):
        pg.postgres_setup(self,fixture,super().setUp)
        from app.services.neostaffing_assignment_schema import sync_staffing_assignment_schema
        sync_staffing_assignment_schema();db.session.commit()

    def test_competing_transfers_accept_one_revision_and_keep_one_active_placement(self):
        person=self.employee();destination=self.deice();values=self.transfer_values(person,destination)
        person_id=person.id;actor_id=User.query.filter_by(username='completion-admin').one().id
        third=staffing.create_unit(dict(unit_type='work_area',name='Fueling',parent_id=destination.parent_id));db.session.commit()
        choices=[destination.id,third.id];ready=Barrier(2)
        def worker(index):
            with self.app.app_context():
                try:
                    actor=db.session.get(User,actor_id);ready.wait(15)
                    transfers.transfer_employee(person_id,actor,{**values,'destination_work_area_id':choices[index]})
                    db.session.commit();return 'saved'
                except transfers.TransferConflict:
                    db.session.rollback();return 'conflict'
                finally:db.session.remove()
        with ThreadPoolExecutor(2) as pool:result=list(pool.map(worker,[0,1]))
        self.assertEqual(sorted(result),['conflict','saved'])
        db.session.remove()
        rows=StaffingWorkAssignment.query.filter_by(person_id=person_id).all()
        self.assertEqual(len(rows),2)
        self.assertEqual(sum(row.active for row in rows),1)
        self.assertIn(next(row.work_area_unit_id for row in rows if row.active),choices)

    def test_profile_edit_and_transfer_cannot_commit_stale_profile_or_placement(self):
        person=self.employee();values=self.transfer_values(person,self.deice())
        profile=contracts.fixtures.EmployeeEditorTest.values(self,person);profile['first_name']='New Profile'
        person_id=person.id;actor_id=User.query.filter_by(username='completion-admin').one().id
        ready=Barrier(2)
        def worker(index):
            with self.app.app_context():
                try:
                    actor=db.session.get(User,actor_id);ready.wait(15)
                    if index:staffing.save_employee_editor(db.session.get(StaffingPerson,person_id),profile)
                    else:transfers.transfer_employee(person_id,actor,values)
                    db.session.commit();return 'saved'
                except ValueError:
                    db.session.rollback();return 'conflict'
                finally:db.session.remove()
        with ThreadPoolExecutor(2) as pool:result=list(pool.map(worker,[0,1]))
        self.assertEqual(sorted(result),['conflict','saved'])
        db.session.remove()
        current=StaffingWorkAssignment.query.filter_by(person_id=person_id,active=True).one()
        saved=db.session.get(StaffingPerson,person_id)
        if result[0]=='saved':
            self.assertEqual(current.work_area_unit_id,int(values['destination_work_area_id']))
            self.assertEqual(saved.first_name,'Editor')
        else:
            self.assertEqual(current.work_area_unit_id,self.area.id)
            self.assertEqual(saved.first_name,'New profile')

    def test_nullable_upgrade_preserves_legacy_rows_and_rejects_undated_nonseasonal(self):
        person=self.employee();identity=(person.id,person.employee_id,person.seniority_date)
        db.session.execute(text(f'ALTER TABLE staffing_people DROP CONSTRAINT {CONSTRAINT}'))
        db.session.execute(text('ALTER TABLE staffing_people ALTER COLUMN seniority_date SET NOT NULL'))
        db.session.commit()
        sync_seniority_schema();db.session.commit();sync_seniority_schema();db.session.commit()
        db.session.expire_all()
        self.assertEqual((person.id,person.employee_id,person.seniority_date),identity)
        self.assertTrue(next(c['nullable'] for c in inspect(db.engine).get_columns('staffing_people') if c['name']=='seniority_date'))
        seasonal=staffing.create_person(dict(employee_id='NULL-PG',first_name='No',last_name='Date',classification='seasonal'))
        db.session.commit();self.assertIsNone(seasonal.seniority_date)
        from sqlalchemy.exc import IntegrityError
        with self.assertRaises(IntegrityError):
            db.session.execute(text("UPDATE staffing_people SET classification='part_time' WHERE id=:id"),{'id':seasonal.id})
            db.session.commit()
        db.session.rollback();self.assertEqual(db.session.get(StaffingPerson,seasonal.id).classification,'seasonal')
