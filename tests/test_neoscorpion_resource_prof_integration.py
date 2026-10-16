"""Real canonical mutations, immutable events and report integration for v4."""
import copy
import unittest
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from tests.neoscorpion_handoff_fixture import submit_and_verify
from app.extensions import db
from app.models import (NeoScorpionFuelAssignment,NeoScorpionFuelWorkState,NeoScorpionFuelingEvent,
    NeoScorpionSortFueler,SortDateMission,SortDateParkingAssignment)
from app.services import neoscorpion as service
from app.services.neoscorpion_prof import event_math,load_observation_jobs,report_context
from app.services.neoscorpion_resource_timing import fueler_work_end
from app.services.neoscorpion_spear import _current_resource_owners
from app.services.neoscorpion_spear_learning_capture import build_completed_learning_record
from app.services.neoscorpion_learning_vault import _validate_learning_record
from tests import test_neoscorpion_operational_timeline as timeline


class ResourceProfIntegrationTest(unittest.TestCase):
    setUp=timeline.OperationalTimelineTest.setUp
    prepare=timeline.OperationalTimelineTest.prepare
    save=timeline.OperationalTimelineTest.save
    off=timeline.OperationalTimelineTest.off
    final_data=timeline.OperationalTimelineTest.final_data
    login=timeline.OperationalTimelineTest.login

    def upcoming(self,*,same_fueler=False):
        fueler=self.fueler if same_fueler else self.fixture._login_user('free-resource-fueler','operator')
        if not same_fueler:db.session.add(NeoScorpionSortFueler(sort_date_operation_id=self.operation.id,user_id=fueler.id))
        mission=self.fixture._mission('UPS902','N492UP',25000,2)
        arrival=SortDateMission(sort_date_operation_id=self.operation.id,sort_date=self.operation.sort_date,
            gateway_code=self.gateway.code,sort_name=self.operation.sort_name,mission_type='arrival',mission_source='manual',
            flight_number='UPS802',origin='SDF',destination=self.gateway.code,assigned_tail_number='N492UP',
            actual_block_in_datetime_utc=self.start-timedelta(minutes=5),arrival_status='arrived')
        db.session.add_all([arrival,SortDateParkingAssignment(sort_date_operation_id=self.operation.id,
            tail_number='N492UP',ramp_code='C',position_code='C04',lane_number=1,assigned_at=self.start)])
        assignment=self.fixture._assignment(mission,truck=self.truck)
        assignment.assigned_fueler_user_id=fueler.id
        assignment.fueler_assigned_at_utc=self.start+timedelta(minutes=1)
        assignment.truck_assigned_at_utc=self.start+timedelta(minutes=2)
        db.session.commit()
        return mission,assignment,fueler

    def finish_first(self):
        self.save(self.start+timedelta(minutes=10),**self.final_data());self.off()
        submit_and_verify(self.gateway,self.dispatcher,self.assignment.id,now_utc=self.start+timedelta(minutes=13));db.session.commit()

    def test_early_preparation_while_truck_busy_freezes_v4_and_keeps_physical_workflow(self):
        self.prepare();mission,assignment,fueler=self.upcoming()
        service.save_fueler_entry(self.gateway,fueler,dict(assignment_id=assignment.id,apu_running='no',
            remaining_left='6',remaining_ctr='0',remaining_right='6'),now_utc=self.start+timedelta(minutes=3));db.session.commit()
        prepared=NeoScorpionFuelWorkState.query.filter_by(fuel_assignment_id=assignment.id).one()
        self.assertIsNone(prepared.manual_on_at_utc)
        self.assertIsNone(prepared.confirmed_on_at_utc)
        self.assertEqual(prepared.performance_context['preparation_evidence']['fueler_id'],fueler.id)
        with self.assertRaisesRegex(ValueError,'TRUCK STILL FUELING'):
            service.confirm_fueler_truck_on(self.gateway,fueler,assignment.id,expected_cycle=1,expected_tail='N492UP',
                expected_truck_id=self.truck.id,now_utc=self.start+timedelta(minutes=4))
        db.session.rollback()
        self.finish_first()
        service.confirm_fueler_truck_on(self.gateway,fueler,assignment.id,expected_cycle=1,expected_tail='N492UP',
            expected_truck_id=self.truck.id,now_utc=self.start+timedelta(minutes=20));db.session.commit()
        self.assertEqual(prepared.performance_context['version'],4)
        self.assertEqual(prepared.performance_context['setup_allowance'],'0')
        evidence=copy.deepcopy(prepared.performance_context['resource_timing'])
        self.assertEqual(evidence['truck_previous_work_id'],self.work.id)
        self.assertEqual(evidence['truck_eligible_start_at'],(self.start+timedelta(minutes=12)).isoformat())
        service.mark_fueler_off(self.gateway,fueler,assignment.id,now_utc=self.start+timedelta(minutes=30));db.session.commit()
        self.assertIsNone(assignment.completed_at_utc)
        service.save_fueler_entry(self.gateway,fueler,dict(assignment_id=assignment.id,**self.final_data()),
            now_utc=self.start+timedelta(minutes=35));db.session.commit()
        self.assertEqual(fueler_work_end(prepared),self.start+timedelta(minutes=35))
        self.assertIsNone(assignment.completed_at_utc)
        submit_and_verify(self.gateway,self.dispatcher,assignment.id,now_utc=self.start+timedelta(minutes=40));db.session.commit()
        event=NeoScorpionFuelingEvent.query.filter_by(fuel_work_state_id=prepared.id).one()
        math=event_math(event)
        self.assertEqual(math['truck_actual_minutes'],18)
        self.assertEqual(math['fueler_actual_minutes'],10)
        self.assertEqual(math['setup_allowance'],0)
        self.assertEqual(Decimal(event.performance_snapshot['resource_timing']['expected_travel_minutes']),2)
        self.assertEqual((math['truck_prof'],math['fueler_prof']),(67,100))
        original=copy.deepcopy(event.performance_snapshot)
        self.settings.assignment_setup_minutes=99;self.settings.spear_truck_minutes_per_ramp_move=99;db.session.commit()
        self.assertEqual(event.performance_snapshot,original)
        self.assertEqual(event_math(event)['truck_prof'],math['truck_prof'])
        projected=next(job for job in load_observation_jobs(self.gateway.id,operation_id=self.operation.id) if job['event'].id==event.id)
        self.assertEqual(projected['truck_prof'],math['truck_prof'])
        self.assertEqual(projected['fueler_prof'],math['fueler_prof'])
        report=report_context(self.gateway,self.operation)
        self.assertIn('v4',report['copy_text']);self.assertIn('Eligible truck staging/start',report['copy_text'])
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),2)
        self.assertEqual(self.selection.current_gallons,4000)
        record=build_completed_learning_record(self.gateway,self.operation,assignment,mission)
        self.assertEqual(record['schema_version'],'spear-learning/v3')
        _validate_learning_record(record)

    def test_postoff_data_releases_fueler_before_dispatcher_complete_no_new_hard_block(self):
        self.prepare();self.off()
        self.assertIsNone(fueler_work_end(self.work))
        self.save(self.start+timedelta(minutes=15),**self.final_data())
        before=copy.deepcopy(self.work.performance_context['fueler_data_ready'])
        row=service.fueler_context(self.gateway,self.fueler)['rows'][0]
        self.assertNotIn(self.fueler.id,_current_resource_owners([row])[0])
        self.assertNotIn(self.truck.id,_current_resource_owners([row])[1])
        self.assertIsNone(self.assignment.completed_at_utc)
        self.save(self.start+timedelta(minutes=16),**self.final_data())
        self.assertEqual(self.work.performance_context['fueler_data_ready'],before)
        submit_and_verify(self.gateway,self.dispatcher,self.assignment.id,now_utc=self.start+timedelta(minutes=20));db.session.commit()
        self.assertEqual(fueler_work_end(self.work),self.start+timedelta(minutes=15))

    def test_v3_active_and_completed_snapshots_do_not_migrate(self):
        self.prepare()
        old=dict(self.work.performance_context,version=3,setup_allowance='8')
        old.pop('resource_timing',None)
        self.work.performance_context=old;db.session.commit()
        self.finish_first()
        event=NeoScorpionFuelingEvent.query.one()
        original=copy.deepcopy(event.performance_snapshot);old_math=event_math(event)
        self.settings.assignment_setup_minutes=999;db.session.commit()
        self.assertEqual(event.performance_snapshot,original)
        self.assertEqual(event.performance_snapshot['version'],3)
        self.assertEqual(event_math(event),old_math)
        self.assertIn('v3',report_context(self.gateway,self.operation)['copy_text'])

    def test_shared_future_preassignments_do_not_get_same_precise_forecast(self):
        self.prepare();self.finish_first();mission,a,f=self.upcoming()
        second=self.fixture._mission('UPS903','N493UP',25000,3)
        b=self.fixture._assignment(second,truck=self.truck);b.assigned_fueler_user_id=f.id
        b.fueler_assigned_at_utc=self.start+timedelta(minutes=14);b.truck_assigned_at_utc=self.start+timedelta(minutes=14)
        db.session.commit()
        rows=[row for row in service.fuel_dispatch_context(self.gateway)['rows'] if row.get('assignment') and row['assignment'].id in {a.id,b.id}]
        self.assertEqual(len(rows),2)
        self.assertTrue(all(row['resource_timing']['forecast_uncertainty']=='Future resource order unproven' for row in rows))
        self.assertTrue(all(row['physical_off_forecast']['likely'] is None for row in rows))

    def test_repeated_tail_visits_keep_resource_evidence_and_inventory_independent(self):
        self.prepare();self.finish_first()
        first=NeoScorpionFuelingEvent.query.one();original=copy.deepcopy(first.performance_snapshot)
        self.mission.assigned_tail_number='N492UP';db.session.commit()
        service.confirm_assignment_tail(self.gateway,self.dispatcher,self.assignment.id,expected_cycle=1,expected_tail='N492UP')
        service.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,expected_cycle=1,
            expected_tail='N492UP',expected_truck_id=self.truck.id,now_utc=self.start+timedelta(minutes=16));db.session.commit()
        b=service._locked_current_fuel_work_state(self.assignment,self.mission)
        identity=dict(physical_work_id=b.id,physical_sequence=b.physical_sequence_number)
        self.save(self.start+timedelta(minutes=17),remaining_left='7',remaining_ctr='0',remaining_right='7',transfer_fuel_gallons='1000')
        self.mission.assigned_tail_number='N491UP';db.session.commit()
        service.confirm_assignment_tail(self.gateway,self.dispatcher,self.assignment.id,expected_cycle=1,expected_tail='N491UP');db.session.commit()
        new=service._locked_current_fuel_work_state(self.assignment,self.mission)
        self.off(at=self.start+timedelta(minutes=19),**identity)
        service.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,expected_cycle=1,
            expected_tail='N491UP',expected_truck_id=self.truck.id,now_utc=self.start+timedelta(minutes=20));db.session.commit()
        self.save(self.start+timedelta(minutes=22),**{**self.final_data(),'transfer_fuel_gallons':'2000','actual_left':'14','actual_right':'14'})
        self.off(at=self.start+timedelta(minutes=30))
        submit_and_verify(self.gateway,self.dispatcher,self.assignment.id,now_utc=self.start+timedelta(minutes=30));db.session.commit()
        current=NeoScorpionFuelingEvent.query.filter_by(fuel_work_state_id=new.id).one()
        self.assertEqual(current.performance_snapshot['resource_timing']['truck_previous_work_id'],b.id)
        self.assertEqual(current.performance_snapshot['resource_timing']['fueler_previous_work_id'],b.id)
        self.assertIsNone(event_math(current)['fueler_prof'])
        frozen=copy.deepcopy(current.performance_snapshot)
        service.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,**identity,
            apu_running='no',actual_left='10',actual_ctr='0',actual_right='10'),now_utc=self.start+timedelta(minutes=31));db.session.commit()
        submit_and_verify(self.gateway,self.dispatcher,self.assignment.id,**identity);db.session.commit()
        events=NeoScorpionFuelingEvent.query.order_by(NeoScorpionFuelingEvent.id).all()
        self.assertEqual(len({e.fuel_work_state_id for e in events}),3)
        self.assertEqual([(e.tail_number,e.transfer_fuel_gallons) for e in events],[('N491UP',3000),('N491UP',2000),('N492UP',1000)])
        self.assertEqual(first.performance_snapshot,original)
        self.assertEqual(current.performance_snapshot,frozen)
        self.assertEqual(self.selection.current_gallons,4000)

    def test_missing_resource_evidence_never_blocks_physical_on(self):
        with patch('app.services.neoscorpion_resource_timing.snapshot_resources',side_effect=ValueError('Synthetic missing evidence')):
            self.prepare()
        self.assertEqual(self.work.manual_on_at_utc,self.start)
        self.assertIn('Resource evidence capture unavailable',self.work.performance_context['resource_timing']['truck_reasons'])

    def test_status_availability_audit_does_not_change_inventory_or_workflow(self):
        from app.services import neoscorpion_assets as assets
        self.prepare();self.finish_first()
        starting,current=self.selection.starting_gallons,self.selection.current_gallons
        for status,minute in (('unavailable_oos',14),('available',18)):
            assets.update_nightly_truck(self.operation,self.truck,status=status,changed_by_user=self.dispatcher,
                now_utc=self.start+timedelta(minutes=minute));db.session.commit()
        history=self.selection.availability_history
        self.assertEqual([(entry['status'],entry['actor_id']) for entry in history[-2:]],
                         [('unavailable_oos',self.dispatcher.id),('available',self.dispatcher.id)])
        for oos in ('1','0'):
            service.save_truck(self.gateway,dict(truck_id=str(self.truck.id),truck_number=self.truck.truck_number,
                is_active='1',is_out_of_service=oos),self.dispatcher);db.session.commit()
        self.assertEqual([entry['available'] for entry in self.truck.availability_history[-2:]],[False,True])
        self.assertEqual((self.selection.starting_gallons,self.selection.current_gallons),(starting,current))
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
