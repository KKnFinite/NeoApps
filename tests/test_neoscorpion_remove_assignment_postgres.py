"""Removal uses the same PostgreSQL locks as Fueler saves and physical ON."""
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from app import create_app
from app.extensions import db
from app.models import Gateway, User, NeoScorpionFuelAssignment, NeoScorpionFuelWorkState, NeoScorpionFuelAuditEntry, NeoScorpionFuelingEvent
from app.services import neoscorpion as service
from tests import test_neoscorpion_remove_assignment as cases
from tests import test_neoscorpion_uplift_defuel as fixtures

URI=os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@unittest.skipUnless(URI,'Requires disposable loopback PostgreSQL')
class RemoveAssignmentPostgresTest(cases.RemoveAssignmentTest):
    def setUp(self):
        url=make_url(URI)
        if url.host not in {'127.0.0.1','localhost'} or not url.database.startswith('neostaffing_test_'):
            raise RuntimeError('Refusing non-test PostgreSQL')
        self.schema='scorpion_remove_'+uuid4().hex
        self.engine=create_engine(URI)
        with self.engine.begin() as conn:conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        def app(config):
            config=type('RemovalPostgresConfig',(config,),dict(SQLALCHEMY_DATABASE_URI=URI,
                SQLALCHEMY_ENGINE_OPTIONS={'connect_args':{'options':f'-csearch_path={self.schema}'}}))
            return create_app(config)
        with patch.object(fixtures,'create_app',app):super().setUp()

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        # Retain synthetic schemas as requested; no database cleanup.
        self.engine.dispose()

    def race(self,action):
        ids=self.gateway.id,self.dispatcher.id,self.fueler.id,self.assignment.id
        db.session.remove()
        def run(index):
            with self.app.app_context():
                db.session.execute(text("SET LOCAL lock_timeout='5s'"))
                g=db.session.get(Gateway,ids[0]);d=db.session.get(User,ids[1]);f=db.session.get(User,ids[2])
                try:
                    result=action(index,g,d,f,ids[3]);db.session.commit();return result
                except ValueError:
                    db.session.rollback();return 'rejected'
                finally:db.session.remove()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(run,(0,1)))

    def test_competing_removals_have_one_audit_and_one_winner(self):
        self.prepare();form=self.form()
        result=self.race(lambda i,g,d,f,aid: 'removed' if service.remove_fuel_assignment(g,d,aid,expected=form).changed else 'noop')
        self.assertEqual(sorted(result),['rejected','removed'])
        self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(field_name='resource_assignments').count(),1)

    def test_physical_on_and_removal_cannot_both_win(self):
        self.prepare();form=self.form();truck_id=self.truck.id
        def action(i,g,d,f,aid):
            if i==0:service.remove_fuel_assignment(g,d,aid,expected=form);return 'removed'
            service.confirm_fueler_truck_on(g,f,aid,expected_cycle=1,expected_tail='N412UP',expected_truck_id=truck_id)
            return 'on'
        result=self.race(action)
        self.assertEqual(result.count('rejected'),1)
        work=NeoScorpionFuelWorkState.query.one();assignment=NeoScorpionFuelAssignment.query.one()
        self.assertEqual(bool(work.manual_on_at_utc),'on' in result)
        self.assertEqual(assignment.assigned_fueler_user_id is None,'removed' in result)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_save_and_removal_keep_all_committed_preparation(self):
        self.prepare();form=self.form()
        baseline=service.fueler_context(self.gateway,self.fueler)['rows'][0]['edit_baseline']
        def action(i,g,d,f,aid):
            if i==0:service.remove_fuel_assignment(g,d,aid,expected=form);return 'removed'
            service.save_fueler_entry(g,f,dict(assignment_id=aid,expected=json.dumps(baseline),notes='Saved before removal'))
            return 'saved'
        result=self.race(action)
        self.assertIn('removed',result)
        work=NeoScorpionFuelWorkState.query.one()
        self.assertEqual(work.notes,'Saved before removal' if 'saved' in result else '')
        self.assertEqual(len(work.tank_states),3)
        self.assertIsNone(work.manual_on_at_utc)
        self.assertEqual(service.fueler_context(db.session.get(Gateway,self.gateway.id),db.session.get(User,self.fueler.id))['rows'],[])
