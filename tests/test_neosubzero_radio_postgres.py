"""Independent PostgreSQL transactions and nullable legacy schema upgrades."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from sqlalchemy import text
from app.extensions import db
from app.models import Gateway,SortDateOperation,NeoSubZeroDepartureDeiceEvent as Event,NeoSubZeroAuditEntry as Audit
from app.services.live_collaboration import entity_version
from app.services.neosubzero_schema import ensure_neosubzero_pretreat_table
from app.services.neosubzero_radio import radio_pending,radio_complete
from tests import test_neosubzero_ucc_postgres as pg
from tests import test_neosubzero_radio as fixtures


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class RadioPostgresTest(unittest.TestCase):
    drop_schema=pg.UccPostgresTest.drop_schema
    prepare=fixtures.RadioCompletionTest.prepare
    mutate=fixtures.RadioCompletionTest.mutate
    finish=fixtures.RadioCompletionTest.finish
    ramp=fixtures.RadioCompletionTest.ramp
    post=fixtures.RadioCompletionTest.post
    test_detached_pending_tail_remains_in_throat_for_identity_review=fixtures.RadioCompletionTest.test_detached_pending_tail_remains_in_throat_for_identity_review

    def setUp(self):
        pg.UccPostgresTest.setUp(self);self.f=self.fixture;self.prepare()
        self.current.stop()
        oid=self.f.operation.id
        self.current=patch('app.neonodes.neosubzero.routes.current_neosubzero_operation',side_effect=lambda _g:db.session.get(SortDateOperation,oid))
        self.current.start();self.addCleanup(self.current.stop)

    def race(self,first,second):
        username=self.actor.username
        barrier=Barrier(2);db.session.remove()
        def worker(data):
            client=self.f.app.test_client()
            login=client.post('/login',data={'username':username,'password':'TestPassword123!'})
            self.assertEqual(login.status_code,302)
            barrier.wait(timeout=10)
            response=client.post('/neosubzero/operations/mutate',data=data)
            return response.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(worker,(first,second)))

    def data(self,event,mission,action,**values):
        return dict(mode='outbound',target='/neosubzero/departure-deice/mutate',board='coordinator',
            operation_id=self.f.operation.id,mission_id=mission.id,physical_tail=mission.assigned_tail_number,
            event_id=event.id,expected_version=entity_version(event),action=action,**values)

    def test_two_confirmations_with_one_version_record_exactly_one_actor_timestamp_and_audit(self):
        event=self.finish();self.mutate(self.f.alpha,'clear')
        eid,cleared=event.id,event.cleared_at_utc
        data=self.data(event,self.f.alpha,'radio_complete')
        self.assertCountEqual(self.race(data,data),[200,409])
        db.session.expire_all();event=db.session.get(Event,eid)
        self.assertTrue(radio_complete(event));self.assertEqual(event.cleared_at_utc,cleared)
        self.assertEqual(Audit.query.filter_by(action='departure_radio_complete').count(),1)
        self.assertEqual(Event.query.count(),2)

    def test_start_cannot_bypass_a_concurrent_radio_confirmation(self):
        event=self.finish();self.mutate(self.f.alpha,'clear')
        next_event=Event.query.filter_by(sort_date_mission_id=self.second.id).one()
        confirm=self.data(event,self.f.alpha,'radio_complete')
        start=self.data(next_event,self.second,'save_treatment',treatment_plan='one_type_i',pass1_surface_area='wings_only',pass1_start='22:21')
        results=self.race(confirm,start)
        self.assertEqual(results[0],200);self.assertIn(results[1],(200,409))
        db.session.expire_all()
        radio=Audit.query.filter_by(action='departure_radio_complete').one()
        if results[1]==200:
            started=Audit.query.filter_by(action='departure_save_treatment',event_id=next_event.id).one()
            self.assertGreater(started.id,radio.id)
        else:self.assertIsNone(db.session.get(Event,next_event.id).pass1_started_at_utc)

    def test_two_stale_clear_submissions_create_pending_once_without_confirmation(self):
        event=self.finish();eid=event.id;data=self.data(event,self.f.alpha,'clear')
        self.assertCountEqual(self.race(data,data),[200,409])
        db.session.expire_all();event=db.session.get(Event,eid)
        self.assertTrue(radio_pending(event));self.assertIsNone(event.radio_completed_at_utc)
        self.assertEqual(Audit.query.filter_by(action='departure_clear').count(),1)

    def test_undo_clear_and_radio_complete_have_exactly_one_winner(self):
        event=self.finish();self.mutate(self.f.alpha,'clear');eid=event.id
        undo=self.data(event,self.f.alpha,'undo_clear',correction_reason='Mistaken CLEAR')
        confirm=self.data(event,self.f.alpha,'radio_complete')
        self.assertCountEqual(self.race(undo,confirm),[200,409])
        db.session.expire_all();event=db.session.get(Event,eid)
        self.assertEqual(Audit.query.filter(Audit.action.in_(['departure_undo_clear','departure_radio_complete'])).count(),1)
        self.f.gateway=db.session.get(Gateway,self.ids[0]);self.f.operation=db.session.get(SortDateOperation,self.ids[1])
        if event.status=='finished':
            self.assertIsNone(event.radio_completed_at_utc);self.assertEqual(self.ramp()['pad']['event'].id,eid)
        else:
            self.assertTrue(radio_complete(event));self.assertIsNone(self.ramp()['pad'])

    def test_competing_starts_cannot_put_two_aircraft_on_pad(self):
        events=[Event.query.filter_by(sort_date_mission_id=m.id).one() for m in (self.f.alpha,self.second)]
        requests=[self.data(e,m,'save_treatment',treatment_plan='one_type_i',pass1_surface_area='wings_only',pass1_start='22:10') for e,m in zip(events,(self.f.alpha,self.second))]
        self.assertCountEqual(self.race(*requests),[200,409])
        db.session.expire_all()
        self.assertEqual(Event.query.filter(Event.pass1_started_at_utc.is_not(None)).count(),1)
        self.f.gateway=db.session.get(Gateway,self.ids[0]);self.f.operation=db.session.get(SortDateOperation,self.ids[1])
        self.assertEqual(len(self.ramp()['active_conflicts']),0)

    def test_nullable_bootstrap_preserves_legacy_clear_and_never_invents_a_confirmation(self):
        event=self.finish();event.status='cleared';db.session.commit()
        before=event.id,event.updated_at,event.pass1_started_at_utc
        db.session.remove()
        with db.engine.begin() as connection:
            connection.execute(text('ALTER TABLE neosubzero_departure_deice_events DROP COLUMN radio_state, DROP COLUMN radio_completed_at_utc, DROP COLUMN radio_completed_by_user_id'))
        with patch.dict(self.f.app.config,{'TESTING':False}):
            self.assertTrue(ensure_neosubzero_pretreat_table(self.f.app))
            self.assertTrue(ensure_neosubzero_pretreat_table(self.f.app))
        event=db.session.get(Event,before[0])
        self.assertEqual((event.id,event.updated_at,event.pass1_started_at_utc),before)
        self.assertIsNone(event.radio_state);self.assertIsNone(event.radio_completed_at_utc)
        self.assertIsNone(event.radio_completed_by_user_id)
        self.assertFalse(radio_pending(event));self.assertFalse(radio_complete(event))
