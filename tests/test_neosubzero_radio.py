"""Canonical spray/CLEAR/radio actions, display-only GONE and evidence safety."""
import copy
import unittest
from datetime import datetime,timedelta
from decimal import Decimal
from unittest.mock import patch
from app.extensions import db
from app.models import (NeoSubZeroDepartureDeiceEvent as Event,NeoSubZeroAuditEntry as Audit,
    NeoSubZeroSprayRecord as Spray,SortDateParkingAssignment,SortDateTailState)
from tests import test_neosubzero_ucc as fixtures
from app.services.neosubzero_departure_deice import mutate_departure_deice,departure_deice_context,departure_deice_revision
from app.services.neosubzero_operations import operations_context
from app.services.neosubzero_ucc import neosubzero_ucc_context,reorder_ucc_queue,NeoSubZeroUccError
from app.services.neosubzero_radio import radio_pending,radio_complete
from app.services.live_collaboration import entity_version


class RadioCompletionTest(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.NeoSubZeroUccTest();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.prepare()

    def prepare(self):
        f=self.f;self.actor=f._user('radio-coordinator','grandmaster')
        self.second=f._departure('UPS-R2','N-R2','SDF',3)
        db.session.add(SortDateParkingAssignment(sort_date_operation_id=f.operation.id,tail_number=self.second.assigned_tail_number,ramp_code='A',position_code='A04'))
        self.mutate(f.alpha,'initial_contact');self.mutate(self.second,'initial_contact')
        f.alpha.actual_block_out_datetime_utc=datetime(2026,9,1,2)
        self.second.actual_block_out_datetime_utc=datetime(2026,9,1,2,1)
        from app.services.neosubzero_spray import set_neosubzero_ucc_truck
        from app.services.neosubzero_trucks import manage_truck
        manage_truck(f.gateway, 'add_truck', {'truck_number':'RADIO-10'}, actor_id=self.actor.id)
        from app.services.neosubzero_ucc import set_neosubzero_ucc_assignment
        set_neosubzero_ucc_truck(f.operation,'Alpha',1,'RADIO-10')
        set_neosubzero_ucc_assignment(f.operation,'Alpha',1,'driver',f.permanent)
        db.session.commit()
        self.client=f.app.test_client();f._login(self.client,self.actor)
        self.current=patch('app.neonodes.neosubzero.routes.current_neosubzero_operation',return_value=f.operation)
        self.current.start();self.addCleanup(self.current.stop)

    def mutate(self,mission,action,**values):
        result=mutate_departure_deice(self.f.operation,mission,action,values,actor_id=self.actor.id)
        db.session.commit();return result

    def finish(self,two=False):
        return self.mutate(self.f.alpha,'save_treatment',treatment_plan='type_i_type_iv' if two else 'one_type_i',
            pass1_surface_area='wings_only',pass1_start='22:10',pass1_end='22:15',
            pass2_surface_area='wings_tail' if two else '',pass2_start='22:16' if two else '',pass2_end='22:20' if two else '')

    def ramp(self):return next(r for r in neosubzero_ucc_context(self.f.gateway,self.f.operation)['ramps'] if r['name']=='Alpha')

    def post(self,event,action,**values):
        data=dict(mode='outbound',target='/neosubzero/departure-deice/mutate',board='coordinator',
            operation_id=self.f.operation.id,mission_id=self.f.alpha.id,physical_tail=self.f.alpha.assigned_tail_number,
            event_id=event.id,expected_version=entity_version(event),action=action)
        data.update(values);return self.client.post('/neosubzero/operations/mutate',data=data)

    def test_full_cycle_retains_throat_until_explicit_radio_and_is_idempotent(self):
        event=self.finish(two=True);self.assertEqual(event.status,'finished')
        original=copy.deepcopy(event.pass_evidence)
        self.mutate(self.f.alpha,'clear');clear=event.cleared_at_utc
        self.assertTrue(radio_pending(event));self.assertFalse(radio_complete(event))
        self.assertIsNone(event.radio_completed_at_utc);self.assertIsNone(event.radio_completed_by_user_id)
        self.assertEqual(self.ramp()['pad']['mission_id'],self.f.alpha.id)
        self.assertEqual(self.ramp()['pad']['visual_state'],'radio-pending')
        row=next(r for r in operations_context(self.f.gateway,self.f.operation,'outbound')['rows'] if r['mission_id']==self.f.alpha.id)
        self.assertIn('100% Type IV',row['script']['text']);self.assertIn('2216',row['script']['text'])
        self.assertEqual(row['visual_state'],'radio-pending');self.assertFalse(row['terminal'])
        with patch('app.neonodes.neosubzero.routes.neosubzero_weather_context',return_value=self.f._weather_context_fixture()):
            for path in ('/neosubzero/coordinator?mission='+str(self.f.alpha.id),'/neosubzero/operations?mode=outbound&mission='+str(self.f.alpha.id)):
                self.assertEqual(self.client.get(path).status_code,200)
        self.assertTrue(radio_pending(event))
        with self.assertRaisesRegex(ValueError,'throat|Position 10'):
            self.mutate(self.second,'save_treatment',treatment_plan='one_type_i',pass1_surface_area='wings_only',pass1_start='22:21')
        db.session.rollback()
        version=self.ramp()['queue_version']
        with self.assertRaises(NeoSubZeroUccError):reorder_ucc_queue(self.f.gateway,self.f.operation,'Alpha',self.second.id,'forward',expected_version=version,user_id=self.actor.id)
        db.session.rollback()
        with self.assertRaisesRegex(ValueError,'RADIO PENDING'):self.mutate(self.f.alpha,'initial_contact')
        db.session.rollback()
        self.assertEqual(self.post(event,'radio_complete').status_code,200)
        self.assertTrue(radio_complete(event));self.assertEqual(event.cleared_at_utc,clear)
        self.assertEqual(event.radio_completed_by_user_id,self.actor.id)
        self.assertGreaterEqual(event.radio_completed_at_utc,clear)
        self.assertEqual(event.pass_evidence,original)
        self.assertIsNone(self.ramp()['pad'])
        self.assertEqual(self.ramp()['throat']['mission_id'],self.second.id)
        before=Audit.query.count();stamp=event.radio_completed_at_utc
        self.assertEqual(self.post(event,'radio_complete').status_code,200)
        self.assertEqual(Audit.query.count(),before);self.assertEqual(event.radio_completed_at_utc,stamp)
        self.assertEqual(Event.query.count(),2)
        self.assertTrue(all(r.gallons is None for r in Spray.query.all()))
        self.assertEqual(Audit.query.filter_by(action='departure_radio_complete').count(),1)
        self.assertEqual(operations_context(self.f.gateway,self.f.operation,'outbound')['rows'][-1]['visual_state'],'gone')

    def test_no_deice_block_out_is_immediate_read_only_and_not_negative_evidence(self):
        m=self.f._departure('UPS-NO','N-NO','DEN',1)
        m.actual_block_out_datetime_utc=datetime.utcnow()
        db.session.add(SortDateTailState(sort_date=self.f.operation.sort_date,gateway_code='RFD',sort_name='night',tail_number='N-NO',pretreat_status=True,deice_status='unknown'));db.session.commit()
        counts=Event.query.count(),Audit.query.count(),Spray.query.count()
        row=next(r for r in operations_context(self.f.gateway,self.f.operation,'outbound')['rows'] if r['mission_id']==m.id)
        self.assertEqual((row['visual_state'],row['visual_label']),('gone','GONE · NO DEICE PLANNED'))
        self.assertTrue(row['pretreat_complete']);self.assertEqual(counts,(Event.query.count(),Audit.query.count(),Spray.query.count()))
        from app.services.frost_operational_evidence import payloads
        self.assertEqual(payloads(self.f.operation),{})
        self.mutate(m,'initial_contact')
        row=next(r for r in operations_context(self.f.gateway,self.f.operation,'outbound')['rows'] if r['mission_id']==m.id)
        self.assertEqual(row['visual_state'],'planned');self.assertFalse(row['gone_without_deice'])

    def test_pilot_required_without_a_plan_and_explicit_negative_remain_active(self):
        m=self.f._departure('UPS-REQ','N-REQ','DEN',1);m.actual_block_out_datetime_utc=datetime.utcnow()
        db.session.add(SortDateTailState(sort_date=self.f.operation.sort_date,gateway_code='RFD',sort_name='night',tail_number='N-REQ',deice_status='required'));db.session.commit()
        row=next(r for r in operations_context(self.f.gateway,self.f.operation,'outbound')['rows'] if r['mission_id']==m.id)
        self.assertNotEqual(row['visual_state'],'gone')
        self.mutate(m,'set_negative')
        row=next(r for r in operations_context(self.f.gateway,self.f.operation,'outbound')['rows'] if r['mission_id']==m.id)
        self.assertEqual(row['visual_state'],'negative')
        self.mutate(m,'clear')
        event=Event.query.filter_by(tail_number='N-REQ').one()
        self.assertIsNone(event.radio_state);self.assertIsNone(event.radio_completed_at_utc)
        self.assertEqual(self.post(event,'radio_complete',mission_id=m.id,physical_tail=m.assigned_tail_number).status_code,409)

    def test_legacy_clear_remains_valid_without_invented_radio_confirmation(self):
        event=self.finish();event.status='cleared';event.cleared_at_utc=datetime(2026,9,1,3,15);db.session.commit()
        before=event.updated_at
        self.assertFalse(radio_pending(event));self.assertFalse(radio_complete(event))
        self.assertIsNone(self.ramp()['pad'])
        self.assertEqual(self.ramp()['throat']['mission_id'],self.second.id)
        self.assertEqual(self.post(event,'radio_complete').status_code,409)
        self.assertEqual(event.updated_at,before);self.assertIsNone(event.radio_completed_at_utc)

    def test_corrections_late_gallons_and_type_i_snapshot_do_not_release_radio(self):
        event=self.finish();self.mutate(self.f.alpha,'clear')
        self.mutate(self.f.alpha,'save_treatment',treatment_plan='one_type_i',pass1_surface_area='wings_only',
            pass1_start='22:11',pass1_end='22:15',correction_reason='Verified actual start correction')
        self.assertTrue(radio_pending(event));self.assertEqual(self.ramp()['pad']['mission_id'],self.f.alpha.id)
        from app.services.neosubzero_spray import set_neosubzero_spray_gallons
        record=Spray.query.filter_by(departure_deice_event_id=event.id).one()
        set_neosubzero_spray_gallons(self.f.operation,self.f.alpha,event,1,1,'12.5',record=record,correction_reason='Late measured gallons');db.session.commit()
        self.assertTrue(radio_pending(event));self.assertEqual(record.gallons,Decimal('12.5'))
        self.assertEqual(self.post(event,'radio_complete').status_code,200)

    def test_stale_tail_sort_and_unauthorized_confirmation_cannot_release_the_throat(self):
        event=self.finish();self.mutate(self.f.alpha,'clear')
        for values in ({'expected_version':'stale'},{'physical_tail':'OTHER'},{'operation_id':-1},{'event_id':-1}):
            with self.subTest(values=values):self.assertEqual(self.post(event,'radio_complete',**values).status_code,409)
        user=self.f._user('radio-only-outbound','watcher');self.f._login(self.client,user)
        self.assertEqual(self.post(event,'radio_complete').status_code,403)
        self.assertTrue(radio_pending(event));self.assertEqual(Audit.query.filter_by(action='departure_radio_complete').count(),0)

    def test_pending_event_follows_physical_tail_across_mission_swap_without_duplicate_occupancy(self):
        event=self.finish();self.mutate(self.f.alpha,'clear')
        old_tail,new_tail=self.f.alpha.assigned_tail_number,self.second.assigned_tail_number
        self.f.alpha.assigned_tail_number=new_tail;self.second.assigned_tail_number=old_tail;db.session.commit()
        # The original mission cannot confirm evidence now owned by its former tail.
        self.assertEqual(self.post(event,'radio_complete',physical_tail=old_tail).status_code,409)
        queue=self.ramp()['queue']
        self.assertEqual(queue[0]['tail'],old_tail);self.assertEqual(queue[0]['mission_id'],self.second.id)
        self.assertEqual(sum(row['tail']==old_tail for row in queue),1)
        self.assertEqual(event.sort_date_mission_id,self.f.alpha.id)
        self.assertEqual(self.post(event,'radio_complete',mission_id=self.second.id,physical_tail=old_tail).status_code,200)
        self.assertEqual(event.sort_date_mission_id,self.f.alpha.id)

    def test_frozen_type_i_readback_survives_later_fluid_setting_change(self):
        from app.services.neosubzero_departure_deice import set_neosubzero_fluid_settings
        event=self.finish();snapshot=copy.deepcopy(event.pass_evidence)
        set_neosubzero_fluid_settings(self.f.gateway,'Different later product',25,'Different later Type IV');db.session.commit()
        self.mutate(self.f.alpha,'clear')
        row=next(r for r in departure_deice_context(self.f.gateway,self.f.operation)['rows'] if r['mission_id']==self.f.alpha.id)
        self.assertEqual(row['script']['fluid'],snapshot['1']['fluid'])
        self.assertEqual(row['script']['concentration'],snapshot['1']['concentration'])
        self.assertNotIn('Different later',row['script']['text'])
        self.assertEqual(self.post(event,'radio_complete').status_code,200)
        self.assertEqual(event.pass_evidence,snapshot)

    def test_detached_pending_tail_remains_in_throat_for_identity_review(self):
        event=self.finish();self.mutate(self.f.alpha,'clear')
        self.f.alpha.assigned_tail_number='N-REPLACED';db.session.commit()
        ramp=self.ramp()
        self.assertEqual(ramp['pad']['tail'],event.tail_number)
        self.assertIn('identity',ramp['pad']['identity_note'].lower())
        self.assertEqual(ramp['pad']['visual_state'],'radio-pending')
        self.assertEqual(sum(row['tail']==event.tail_number for row in ramp['aircraft']),1)
        # A later mission reset or parking move cannot erase the recorded pad
        # occupation. Retain it from the immutable pass location, not a made-up
        # Block-Out timestamp on the replacement aircraft.
        self.f.alpha.actual_block_out_datetime_utc=None
        parking=SortDateParkingAssignment.query.filter_by(sort_date_operation_id=self.f.operation.id,tail_number=event.tail_number).one()
        db.session.delete(parking);db.session.commit()
        self.assertEqual(self.ramp()['pad']['tail'],event.tail_number)
        self.assertIsNone(self.ramp()['pad']['block_out_at_utc'])
        self.assertEqual(self.post(event,'radio_complete',physical_tail=event.tail_number).status_code,409)
        with self.assertRaisesRegex(ValueError,'RADIO COMPLETE|Position 10'):
            self.mutate(self.second,'save_treatment',treatment_plan='one_type_i',pass1_surface_area='wings_only',pass1_start='22:21')
        db.session.rollback()
