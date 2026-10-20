"""Status is a read-only projection of data, resources and physical evidence."""
from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest

from app.extensions import db
from app.models import NeoScorpionFuelingEvent, NeoScorpionSortFueler
from app.services import neoscorpion as fuel
from app.services.neoscorpion_fueling_status import fueling_status
from tests import test_neoscorpion_fueler_submission as fixtures


class StatusRulesTest(unittest.TestCase):
    def row(self):
        return dict(mission=SimpleNamespace(assigned_tail_number='N491UP', flight_number='UPS901',
            destination='MSP', eta_datetime_utc=None, planned_datetime_utc=datetime(2026,10,18,7)),
            tail_number='N491UP', assignment=SimpleNamespace(assigned_fueler_user_id=None,
            assigned_truck_id=None, review_status='pending'), fuel_work_state=None,
            inbound_fuel_lbs=0, required_fuel_lbs=25000, parking_valid=True, arrival_status='Not Arrived')

    def test_each_essential_field_missing_is_pending_even_with_partial_assignment(self):
        for field in ('tail', 'flight', 'destination', 'departure', 'required', 'inbound', 'parking'):
            for resource in ('none', 'fueler', 'truck'):
                with self.subTest(field=field,resource=resource):
                    r=self.row()
                    if field=='tail':r['tail_number']='-'
                    elif field=='flight':r['mission'].flight_number=''
                    elif field=='destination':r['mission'].destination=''
                    elif field=='departure':r['mission'].planned_datetime_utc=None
                    elif field=='required':r['required_fuel_lbs']=None
                    elif field=='inbound':r['inbound_fuel_lbs']=None
                    else:r['parking_valid']=False
                    if resource=='fueler':r['assignment'].assigned_fueler_user_id=1
                    elif resource=='truck':r['assignment'].assigned_truck_id=2
                    s=fueling_status(r)
                    self.assertEqual(s['dispatch_status_key'],'pending')
                    self.assertEqual(len(s['mission_data_missing']),1)
                    self.assertEqual(s['fuel_status_secondary'],'MISSION DATA MISSING')

    def test_ready_does_not_require_arrival_and_uses_zero_or_verified_fuel(self):
        for arrival in ('Not Arrived','On Ground','Arrived','Cancelled','Unknown'):
            r=self.row();r['arrival_status']=arrival
            self.assertEqual(fueling_status(r)['dispatch_status_key'],'ready')
        for key in ('measured_inbound_fuel_lbs','tail_swap_inherited_fuel_lbs'):
            r=self.row();r.update(inbound_fuel_lbs=None,**{key:0})
            self.assertEqual(fueling_status(r)['dispatch_status_key'],'ready')
        r=self.row();r.update(inbound_fuel_lbs=None,estimated_fuel_gallons=0)
        self.assertEqual(fueling_status(r)['dispatch_status_key'],'pending')

    def test_advance_assignment_is_not_data_readiness_or_physical_on(self):
        r=self.row();r.update(inbound_fuel_lbs=None,required_fuel_lbs=None,parking_valid=False)
        r['assignment'].assigned_fueler_user_id=1;r['assignment'].assigned_truck_id=2
        r['truck_start_reason']='TRUCK STILL FUELING'
        s=fueling_status(r)
        self.assertEqual(s['dispatch_status_key'],'assigned')
        self.assertEqual(s['fuel_status_secondary'],'MISSION DATA MISSING')
        self.assertEqual(s['mission_data_summary'],'NEEDS FUEL LOAD +2')
        self.assertIn('WAITING FOR TRUCK · TRUCK STILL FUELING',s['fuel_status_warnings'])
        self.assertEqual(len(s['mission_data_missing']),3)
        r.update(inbound_fuel_lbs=0,required_fuel_lbs=25000,parking_valid=True)
        self.assertEqual(fueling_status(r)['dispatch_status_detail'],'WAITING FOR TRUCK · TRUCK STILL FUELING')

    def test_movement_and_fob_advice_never_hide_missing_data(self):
        for movement in ('TRUCK STILL FUELING','TRUCK UNAVAILABLE','TRUCK ON PENDING','COMING FROM C07'):
            r=self.row();r['assignment'].assigned_truck_id=2;r['assignment'].assigned_fueler_user_id=1
            r.update(required_fuel_lbs=None,truck_start_reason=movement,
                     truck_travel_until='2026-10-18T06:00:00Z',fob_likely=True)
            s=fueling_status(r)
            self.assertEqual(s['dispatch_status_key'],'assigned')
            self.assertEqual(s['fuel_status_secondary'],'MISSION DATA MISSING')
            self.assertEqual(s['mission_data_summary'],'NEEDS FUEL LOAD')
            self.assertFalse(fuel._ready_to_assign(r))

    def test_missing_summary_full_list_and_reversible_readiness(self):
        r=self.row();r.update(required_fuel_lbs=None,inbound_fuel_lbs=None)
        s=fueling_status(r)
        self.assertEqual(s['mission_data_missing'],('Required Fuel Load','Inbound Fuel / verified measurement'))
        self.assertEqual(s['mission_data_summary'],'NEEDS FUEL LOAD +1')
        r['required_fuel_lbs']=25000
        self.assertEqual(fueling_status(r)['mission_data_summary'],'NEEDS INBOUND')
        r['inbound_fuel_lbs']=0
        self.assertEqual(fueling_status(r)['dispatch_status_key'],'ready')
        r['inbound_fuel_lbs']=None
        self.assertEqual(fueling_status(r)['dispatch_status_key'],'pending')

    def test_physical_milestones_precede_missing_data_but_not_real_review(self):
        r=self.row();r.update(inbound_fuel_lbs=None,parking_valid=False)
        r['fuel_work_state']=SimpleNamespace(on_at_utc=datetime(2026,10,18,1),off_at_utc=None)
        r['fueling_on_active']=False
        self.assertEqual(fueling_status(r)['dispatch_status_key'],'pending')
        r['fueling_on_active']=True
        self.assertEqual(fueling_status(r)['dispatch_status_key'],'fueling')
        r['fueling_on_trusted']=False
        self.assertEqual(fueling_status(r)['dispatch_status_key'],'review')
        self.assertIn('Physical ON evidence requires verification',fueling_status(r)['status_review_reasons'])
        r['fueling_on_trusted']=True
        r['fuel_work_state'].off_at_utc=datetime(2026,10,18,2)
        self.assertEqual(fueling_status(r)['dispatch_status_key'],'off')
        for evidence in ({'direction_mismatch':True},{'physical_transfer_review':True},
                         {'effective_hold':True},{'work_ended_early':True}):
            self.assertEqual(fueling_status(dict(r,**evidence))['dispatch_status_key'],'review')
        r['fuel_work_state'].continuation_review_reason='Source correction requires handoff review'
        self.assertEqual(fueling_status(r)['dispatch_status_key'],'review')

    def test_fob_is_verified_not_merely_likely_and_advisory_swap_is_not_hold(self):
        r=self.row();r['assignment'].assigned_fueler_user_id=1
        r.update(fob_likely=True,tail_mismatch=True)
        self.assertEqual(fueling_status(r)['dispatch_status_key'],'ready')
        self.assertEqual(fueling_status(r)['fuel_status_secondary'],'FOB CHECK')
        r['fuel_on_board_ready']=True
        self.assertEqual(fueling_status(r)['dispatch_status_key'],'fob-ready')
        r['fuel_on_board_complete']=True
        self.assertEqual(fueling_status(r)['dispatch_status_key'],'fob')
        self.assertFalse(fueling_status(r)['fuel_status_warnings'])
        r['direction_mismatch']=True
        self.assertEqual(fueling_status(r)['dispatch_status_key'],'fob')
        self.assertIn('Fuel direction discrepancy',fueling_status(r)['fuel_status_warnings'])

    def test_readiness_pulse_and_colors_use_only_actual_stage(self):
        r=self.row()
        self.assertTrue(fuel._ready_to_assign(r))
        r['required_fuel_lbs']=None
        self.assertFalse(fuel._ready_to_assign(r))
        r['assignment'].assigned_fueler_user_id=1;r['assignment'].assigned_truck_id=2
        self.assertEqual(fueling_status(r,now=datetime(2026,10,18,6))['fuel_status_color'],'orange')
        self.assertEqual(fueling_status(r,now=datetime(2026,10,18,6,45))['fuel_status_color'],'red')
        self.assertFalse(fuel._ready_to_assign(r))

    def test_truck_physical_status_and_serviceability_are_independent(self):
        now=datetime(2026,10,18,3)
        record=dict(id=1,truck_id=2,fueler_id=3,manual_on=now-timedelta(minutes=10),
            confirmed_on=None,on=None,release=None,assigned_at=now-timedelta(minutes=20),
            parking='C09',truck_available=True,remaining_at_on=False)
        future=dict(record,id=2,manual_on=None,parking='C07')
        assignments=[SimpleNamespace(id=i,operational_status='active',review_status='assigned',
            sort_date_mission=SimpleNamespace(fuel_status='pending')) for i in (1,2)]
        for status,expected in (('available','FUELING · C09'),('topping_off','FUELING · C09 · RETURN REVIEW'),
                                ('needs_sump','FUELING · C09 · NEEDS SUMP REVIEW'),('unavailable_oos','FUELING · C09 · OOS REVIEW')):
            selection=SimpleNamespace(status=status);visuals=[dict(truck_id=2)]
            fuel._attach_dispatch_truck_activity(visuals,assignments,dict(records=[record,future],releases=[]),
                {2:selection},None,now_utc=now)
            self.assertEqual(visuals[0]['activity_label'],expected)
            self.assertEqual(selection.status,status)
        automatic=dict(record,manual_on=None,confirmed_on=record['manual_on'])
        visuals=[dict(truck_id=2)]
        fuel._attach_dispatch_truck_activity(visuals,assignments,dict(records=[automatic,future],releases=[]),
            {2:SimpleNamespace(status='available')},None,now_utc=now)
        self.assertEqual(visuals[0]['activity_label'],'FUELING · C09')
        assignments[0].operational_status='hold_review'
        fuel._attach_dispatch_truck_activity(visuals,assignments,dict(records=[record,future],releases=[]),
            {2:SimpleNamespace(status='available')},None,now_utc=now)
        self.assertEqual(visuals[0]['activity_label'],'FUELING · C09')


class StatusIntegrationTest(unittest.TestCase):
    setUp=fixtures.SubmissionTest.setUp
    prepare=fixtures.SubmissionTest.prepare
    save=fixtures.SubmissionTest.save
    off=fixtures.SubmissionTest.off
    final_data=fixtures.SubmissionTest.final_data
    baseline=fixtures.SubmissionTest.baseline
    row=fixtures.SubmissionTest.row
    submit=fixtures.SubmissionTest.submit
    login=fixtures.SubmissionTest.login

    def test_pending_advance_assignment_allowed_and_missing_fields_visible_in_action_center(self):
        self.mission.planned_fuel_load=None;self.tail.inbound_fuel_lbs=None
        self.arrival.arrival_status='scheduled';db.session.commit()
        r=self.row();self.assertEqual(r['dispatch_status_key'],'pending');self.assertFalse(r['ready_to_assign'])
        truck=self.fixture._nightly_truck()
        db.session.add(NeoScorpionSortFueler(sort_date_operation_id=self.operation.id,user_id=self.fueler.id));db.session.commit()
        fuel.save_dispatch_assignment(self.gateway,dict(mission_id=self.mission.id,
            expected_work_ref='',expected_assigned_fueler_user_id='',expected_assigned_truck_id='',
            assigned_fueler_user_id=self.fueler.id,assigned_truck_id=truck.id));db.session.commit()
        r=self.row();self.assertEqual(r['dispatch_status_key'],'assigned')
        self.assertTrue(r['mission_data_action']);self.assertFalse(r['normal_completion_ready'])
        self.assertIsNone(r['fuel_work_state'].on_at_utc if r['fuel_work_state'] else None)
        self.login(self.dispatcher)
        html=self.client.get('/neoscorpion/fuel-dispatch').get_data(as_text=True)
        self.assertNotIn('>NOT READY<',html)
        self.assertIn('data-action-key="mission-data"',html)
        self.assertIn('Required Fuel Load',html);self.assertIn('Inbound Fuel / verified measurement',html)
        self.assertNotIn('id="scorpion-complete-',html)
        fr=next(item for item in fuel.fueler_context(self.gateway,self.fueler)['rows']
                if item['mission'].id==self.mission.id)
        for key in ('dispatch_status_key','mission_data_missing','mission_data_summary'):
            self.assertEqual(fr[key],r[key])
        self.login(self.fueler)
        fueler_html=self.client.get('/neoscorpion/fueler').get_data(as_text=True)
        self.assertIn('NEEDS FUEL LOAD +1',fueler_html)
        self.assertNotIn('>NOT READY<',fueler_html)

    def test_off_submission_and_verification_remain_independent_with_one_event(self):
        self.prepare();self.assertEqual(self.row()['dispatch_status_key'],'fueling')
        self.off();r=self.row()
        self.assertEqual(r['dispatch_status_key'],'off');self.assertFalse(r['normal_completion_ready'])
        self.assertIn('Remaining',r['completion_progress'])
        self.save(self.start+timedelta(minutes=15),**self.final_data())
        self.assertEqual(self.row()['dispatch_status_key'],'off');self.assertFalse(self.row()['normal_completion_ready'])
        self.submit();r=self.row()
        self.assertEqual(r['dispatch_status_key'],'off');self.assertTrue(r['normal_completion_ready'])
        self.assertIn('ready for Dispatcher COMPLETE',r['completion_progress'])
        self.login(self.dispatcher)
        html=self.client.get('/neoscorpion/fuel-dispatch').get_data(as_text=True)
        self.assertIn(f'id="scorpion-complete-{self.mission.id}"',html)
        self.assertNotIn('>NOT READY<',html)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assertEqual(self.row()['dispatch_status_key'],'complete')
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)

    def test_review_is_actionable_without_changing_hold_or_physical_timestamps(self):
        self.prepare();on=self.work.manual_on_at_utc
        self.work.physical_transfer_review=True;db.session.commit()
        r=self.row()
        self.assertEqual(r['dispatch_status_key'],'review');self.assertTrue(r['status_review_action'])
        self.assertFalse(r['normal_completion_ready']);self.assertEqual(self.work.manual_on_at_utc,on)
        self.assertNotEqual(self.assignment.operational_status,'hold_review')
        self.assertTrue(r['off_ready'])  # Review presentation never holds the truck hostage.

    def test_shared_status_template_preserves_missing_data_during_travel(self):
        from flask import current_app, render_template_string
        r=self.row()
        r['assignment'].assigned_fueler_user_id=self.fueler.id
        r['assignment'].assigned_truck_id=123
        r.update(required_fuel_lbs=None,truck_start_reason='COMING FROM C07',
                 truck_travel_until='2026-10-18T09:00:00Z',truck_travel_origin='C07')
        r.update(fueling_status(r))
        for dispatcher in (True,False):
            with current_app.test_request_context():
                html=render_template_string('{% from "neonodes/neoscorpion/_fuel_status.html" import fuel_status with context %}{{ fuel_status(row,dispatcher=dispatcher) }}',row=r,dispatcher=dispatcher)
            self.assertIn('NEEDS FUEL LOAD',html)
            self.assertIn('title="Missing: Required Fuel Load',html)
            self.assertNotIn('data-truck-travel-until',html)
            self.assertNotIn('SEE DETAILS',html)
