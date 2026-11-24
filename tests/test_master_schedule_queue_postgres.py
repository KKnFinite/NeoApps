"""Competing atomic board edits through the normal authenticated PostgreSQL route."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time
from threading import Barrier
from unittest import TestCase, skipUnless
from unittest.mock import patch

from app.extensions import db
from app.models import MasterFlightSchedule, SortDateMission, NeoScorpionSortAssetState
from app.neomotherbrain import routes
from tests import test_motherbrain_routes as fixtures
from tests import test_motherbrain_block_out_authority_postgres as pg


@skipUnless(pg.URI, 'Requires disposable loopback PostgreSQL')
class MasterQueueRaceTest(TestCase):
    def setUp(self):
        self.case = fixtures.MotherBrainRoutesTest()
        pg.postgres_setup(self, fixtures, self.case.setUp)
        self.addCleanup(self.case.tearDown)

    def test_bulk_current_sort_create_retire_restore_and_deleted_row_conflict(self):
        case = self.case
        case.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE'] = datetime(2026,11,23,22)
        case._set_sort_window('night',time(21),time(5))
        op = case._operation(sort_date=date(2026,11,23))
        db.session.add(op)
        flights = [case._add_master(flight_number='UPS8801',mission_type='arrival',aircraft_type='757'),
                   case._add_master(flight_number='UPS8802',aircraft_type='757')]
        db.session.commit()
        headers = {'Accept':'application/json','X-Requested-With':'fetch'}
        def post_days(days):
            data = case._bulk_master_schedule_form_data(*[
                dict(id=m.id,mission_type=m.mission_type,flight_number=m.flight_number,origin=m.origin,
                     destination=m.destination,active_days=days) for m in flights])
            data.update(board_mission_type='mixed',queue_save='1')
            data.update({f'version_{m.id}':m.updated_at.isoformat() for m in flights})
            result = case.client.post('/motherbrain/master-schedule',data=data,headers=headers)
            db.session.expire_all()
            return result
        self.assertEqual(post_days(['monday']).status_code,200)
        def missions():
            return {m.id:(m.arrival_status if m.mission_type=='arrival' else m.departure_status)
                    for m in SortDateMission.query.filter_by(sort_date_operation_id=op.id)}
        original = missions()
        self.assertEqual(len(original),2)
        self.assertEqual(post_days(['tuesday']).status_code,200)
        self.assertEqual(missions(),dict.fromkeys(original,'cancelled'))
        self.assertEqual(post_days(['monday','wednesday']).status_code,200)
        self.assertEqual(missions(),original)
        self.assertEqual(NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=op.id).one().revision,6)
        case.client.post(f'/motherbrain/master-schedule/{flights[1].id}/delete')
        result = post_days(['sunday'])
        self.assertEqual(result.status_code,409)
        self.assertEqual(result.get_json()['conflict_id'],str(flights[1].id))
        self.assertTrue(result.get_json()['unavailable'])
        self.assertEqual(flights[0].active_days,'monday,wednesday')

    def test_competing_batches_rollback_every_losing_row(self):
        case = self.case
        first = case.client
        case.client = case.app.test_client()
        case._login_motherbrain_role('schedule-operator', 'grandmaster')
        second = case.client
        masters = [case._add_master(flight_number=f'TST{i}', aircraft_type='757') for i in range(3)]
        db.session.commit()
        headers = {'Accept':'application/json','X-Requested-With':'fetch'}
        def batch(pair, day):
            data = case._bulk_master_schedule_form_data(*[
                dict(id=m.id,flight_number=m.flight_number,active_days=[day]) for m in pair])
            data.update(board_mission_type='mixed',queue_save='1')
            data.update({f'version_{m.id}':m.updated_at.isoformat() for m in pair})
            return data
        ids = [m.id for m in masters]
        inputs = [batch([masters[1],masters[0]],'friday'),batch([masters[2],masters[0]],'saturday')]
        gate = Barrier(2)
        original = routes.lock_master_editor
        def meet_at_lock(*args, **kwargs):
            gate.wait(timeout=10)
            return original(*args, **kwargs)
        with patch.object(routes,'lock_master_editor',side_effect=meet_at_lock), ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(client.post,'/motherbrain/master-schedule',data=data,headers=headers)
                       for client,data in zip([first,second],inputs)]
            responses = [f.result(timeout=20) for f in futures]
        self.assertEqual(sorted(r.status_code for r in responses),[200,409])
        winner = int(responses[0].status_code != 200)
        loser = 1-winner
        self.assertEqual(responses[loser].get_json()['conflict_id'],ids[0])
        db.session.expire_all()
        saved = [db.session.get(MasterFlightSchedule,key).active_days for key in ids]
        day = ['friday','saturday'][winner]
        self.assertEqual(saved[0],day)
        self.assertEqual(saved[winner+1],day)
        self.assertEqual(saved[loser+1],'monday,tuesday')

        # A missing optimistic token never authorizes an overwrite.
        data = batch([masters[0]],'sunday')
        del data[f'version_{ids[0]}']
        self.assertEqual(first.post('/motherbrain/master-schedule',data=data,headers=headers).status_code,400)
        case._login_motherbrain_role('schedule-observer','watcher')
        denied = case.client.post('/motherbrain/master-schedule',data=data,headers=headers)
        self.assertEqual(denied.status_code,302)
        with case.app.test_request_context():
            from flask import url_for
            self.assertEqual(denied.location,url_for('neomotherbrain.rfd_hub'))
        db.session.expire_all()
        self.assertEqual(masters[0].active_days,day)
