"""Batch 3 physical milestones, retained facts, PROF and SPEAR contracts."""
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import event, inspect, text
from app.extensions import db
from tests.neoscorpion_handoff_fixture import submit_and_verify
from app.models import (NeoScorpionFuelingEvent, NeoScorpionFuelWorkState,
                        NeoScorpionSortTruck, NeoScorpionSortFueler, NeoScorpionTruckInventoryAudit, SortDateOperation)
from app.services import neoscorpion as service
from app.services.neoscorpion_assets import verify_nightly_truck_inventory
from app.services.neoscorpion_prof import setup_allowance, event_math, totals, load_jobs, load_observation_jobs, report_context
from app.services.neoscorpion_spear_forecasts import physical_off_forecast
from app.services.neoscorpion_spear import _current_resource_owners
from app.services.neoscorpion_spear_calibration import build_live_calibration
from app.services.neoscorpion_spear_learning_capture import build_completed_learning_record
from app.services.neoscorpion_learning_vault import _validate_learning_record
from app.services.schema_sync import sync_local_sqlite_schema
from tests import test_neoscorpion_fueling_status as fixtures


class OperationalTimelineTest(unittest.TestCase):
    setUp = fixtures.FuelingStatusIntegrationTest.setUp
    login = fixtures.FuelingStatusIntegrationTest.login

    def prepare(self, lead=15):
        self.start = datetime.utcnow().replace(microsecond=0)-timedelta(minutes=30)
        self.truck = self.fixture._nightly_truck()
        self.selection = NeoScorpionSortTruck.query.filter_by(fuel_truck_id=self.truck.id).one()
        self.assignment.assigned_fueler_user_id = self.fueler.id
        self.assignment.assigned_truck_id = self.truck.id
        self.assignment.fueler_assigned_at_utc = self.start-timedelta(minutes=5+lead)
        self.assignment.truck_assigned_at_utc = self.start-timedelta(minutes=5)
        self.assignment.resource_assignment_by_user_id = self.dispatcher.id
        self.settings.assignment_setup_minutes = 8
        self.settings.advance_setup_window_minutes = 10
        db.session.add(NeoScorpionSortFueler(sort_date_operation_id=self.operation.id,user_id=self.fueler.id))
        db.session.commit()
        service.confirm_fueler_truck_on(self.gateway, self.fueler, self.assignment.id,
            expected_cycle=1, expected_tail=self.mission.assigned_tail_number,
            expected_truck_id=self.truck.id, now_utc=self.start)
        db.session.commit()
        self.work = NeoScorpionFuelWorkState.query.filter_by(fuel_assignment_id=self.assignment.id).one()

    def save(self, at, **extra):
        result = service.save_fueler_entry(self.gateway, self.fueler,
            dict(assignment_id=self.assignment.id, **extra), now_utc=at)
        db.session.commit()
        return result

    def final_data(self):
        return dict(apu_running="no", remaining_left="6", remaining_ctr="0", remaining_right="6",
                    actual_left="15", actual_ctr="0", actual_right="15", transfer_fuel_gallons="3000")

    def off(self, at=None, **kwargs):
        result = service.mark_fueler_off(self.gateway, self.fueler, self.assignment.id,
                                       now_utc=at or self.start+timedelta(minutes=12), **kwargs)
        db.session.commit()
        return result

    def test_zero_or_full_setup_boundaries_and_unknown(self):
        now = datetime(2026,10,13,23,55)
        for lead, expected in ((20,0),(12,0),(10,0),(5,8),(0,8)):
            self.assertEqual(setup_allowance(now-timedelta(minutes=lead),now,10,8)[0], expected)
        self.assertEqual(setup_allowance(None,None,None,8)[0],8)
        self.assertIsNone(setup_allowance(None,now,10,8)[0])

    def test_advance_window_settings_permissions_validation_clear_and_persistence(self):
        self.login(self.fueler)
        form=dict(action="save_assignment_planning_settings",advance_setup_window_minutes="10",
                  assignment_setup_minutes="8",assignment_finishing_minutes="5",assignment_eta_safety_buffer_minutes="5")
        self.assertEqual(self.client.post("/neoscorpion/settings",data=form).status_code,403)
        # Dispatch edit authority alone is not Settings edit authority.
        manager=self.fixture._login_user("timeline-settings-master","master")
        self.login(manager)
        self.assertEqual(self.client.post("/neoscorpion/settings",data=form).status_code,302)
        db.session.expire_all()
        self.assertEqual(self.settings.advance_setup_window_minutes,10)
        for invalid in ("-1","1.5","unknown"):
            self.assertEqual(self.client.post("/neoscorpion/settings",data={**form,"advance_setup_window_minutes":invalid}).status_code,400)
            self.assertEqual(self.settings.advance_setup_window_minutes,10)
        self.assertEqual(self.client.post("/neoscorpion/settings",data={**form,"advance_setup_window_minutes":""}).status_code,302)
        self.assertIsNone(self.settings.advance_setup_window_minutes)
        self.assertEqual(setup_allowance(None,None,self.settings.advance_setup_window_minutes,self.settings.assignment_setup_minutes)[0],8)

    def test_off_before_actuals_releases_truck_keeps_final_data_editable_and_completes_once(self):
        self.prepare()
        self.off()
        self.assertIsNone(self.assignment.completed_at_utc)
        self.assertEqual(self.work.off_at_utc, self.start+timedelta(minutes=12))
        self.assertFalse(self.selection.inventory_verified)
        self.assertEqual(self.selection.current_gallons,10000)
        row = service.fueler_context(self.gateway,self.fueler)["rows"][0]
        self.assertEqual(row["dispatch_status_key"],"off")
        self.assertIn(self.assignment.assigned_fueler_user_id, _current_resource_owners([row])[0])
        self.assertNotIn(self.truck.id, _current_resource_owners([row])[1])
        self.off(at=self.start+timedelta(minutes=14))
        self.assertEqual(self.work.off_at_utc,self.start+timedelta(minutes=12))
        self.save(self.start+timedelta(minutes=15),**self.final_data())
        self.assertIsNone(self.assignment.completed_at_utc)
        ready=service.fuel_dispatch_context(self.gateway)["rows"][0]
        self.assertEqual(ready["dispatch_status_key"],"off")
        self.assertTrue(ready["normal_completion_ready"])
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id,
            now_utc=self.start+timedelta(minutes=15))
        db.session.commit()
        self.assertEqual(self.assignment.completed_at_utc,self.start+timedelta(minutes=15))
        self.assertEqual(self.selection.current_gallons,7000)
        self.assertTrue(self.selection.inventory_verified)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.assertFalse(service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id).changed)
        math = event_math(NeoScorpionFuelingEvent.query.one())
        self.assertEqual(math["wrap_minutes"],3)
        self.assertEqual(math["actual_minutes"],12)
        self.assertEqual(math["setup_allowance"],0)
        self.assertEqual(math["fueler_prof"],83)
        self.assertEqual(math["truck_prof"],83)
        self.assertEqual(service.fueler_context(self.gateway,self.fueler)["rows"],[])
        self.assertFalse(self.save(self.start+timedelta(minutes=16),**self.final_data()).changed)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)

    def test_actuals_before_off_zero_wrapup_full_setup_and_separate_truck_prof(self):
        self.prepare(lead=5)
        self.save(self.start+timedelta(minutes=10),**self.final_data())
        self.assertIsNone(self.assignment.completed_at_utc)
        self.off()
        self.assertIsNone(self.assignment.completed_at_utc)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        ready=service.fuel_dispatch_context(self.gateway)["rows"][0]
        self.assertEqual(ready["dispatch_status_key"],"off")
        self.assertTrue(ready["normal_completion_ready"])
        service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id,
            now_utc=self.start+timedelta(minutes=12))
        db.session.commit()
        math = event_math(NeoScorpionFuelingEvent.query.one())
        self.assertEqual((math["wrap_minutes"],math["admin_minutes"]),(0,0))
        self.assertEqual(math["fueler_prof"],150)
        self.assertEqual(math["truck_prof"],83)
        self.assertEqual(math["expected_fueler"],18)
        self.assertEqual(math["expected_truck"],10)

    def test_other_admin_delay_is_separate_from_actual_wrap(self):
        self.prepare()
        self.off()
        data=self.final_data(); data.pop("transfer_fuel_gallons")
        self.save(self.start+timedelta(minutes=15),**data)
        self.assertIsNone(self.assignment.completed_at_utc)
        self.save(self.start+timedelta(minutes=20),transfer_fuel_gallons="3000")
        math=event_math(NeoScorpionFuelingEvent.query.one())
        self.assertEqual((math["wrap_minutes"],math["admin_minutes"]),(3,5))
        self.assertEqual(math["actual_minutes"],12)

    def test_new_verified_reading_after_off_covers_transfer_without_double_deduction(self):
        self.prepare(); self.off()
        verify_nightly_truck_inventory(self.operation,self.truck,7000,changed_by_user=self.dispatcher)
        db.session.commit()
        self.save(self.start+timedelta(minutes=15),**self.final_data())
        self.assertEqual(self.selection.current_gallons,7000)
        self.assertTrue(self.selection.inventory_verified)
        self.assertIn("no second deduction",NeoScorpionTruckInventoryAudit.query.order_by(NeoScorpionTruckInventoryAudit.id.desc()).first().reason)

    def test_insufficient_inventory_completes_with_discrepancy_and_preserved_last_reading(self):
        self.prepare(); self.selection.current_gallons=100; db.session.commit()
        self.off(); self.save(self.start+timedelta(minutes=15),**self.final_data())
        self.assertEqual(self.selection.current_gallons,100)
        self.assertFalse(self.selection.inventory_verified)
        self.assertEqual(NeoScorpionTruckInventoryAudit.query.filter_by(action="discrepancy").count(),1)

    def test_multiple_pending_jobs_post_once_out_of_order_without_guessing_available_fuel(self):
        self.prepare(); self.off()
        mission=self.fixture._mission("UPS902","N492UP",25000,2)
        assignment=self.fixture._assignment(mission,truck=self.truck)
        assignment.assigned_fueler_user_id=self.dispatcher.id
        assignment.fueler_assigned_at_utc=assignment.truck_assigned_at_utc=self.start+timedelta(minutes=13)
        db.session.commit()
        service.confirm_fueler_truck_on(self.gateway,self.dispatcher,assignment.id,expected_cycle=1,
            expected_tail="N492UP",expected_truck_id=self.truck.id,now_utc=self.start+timedelta(minutes=13))
        service.save_fueler_entry(self.gateway,self.dispatcher,dict(assignment_id=assignment.id,**self.final_data()),now_utc=self.start+timedelta(minutes=23))
        service.mark_fueler_off(self.gateway,self.dispatcher,assignment.id,now_utc=self.start+timedelta(minutes=25))
        db.session.commit()
        self.assertEqual(self.selection.current_gallons,7000)
        self.assertFalse(self.selection.inventory_verified)
        self.save(self.start+timedelta(minutes=30),**self.final_data())
        self.assertEqual(self.selection.current_gallons,4000)
        self.assertTrue(self.selection.inventory_verified)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),2)

    def test_uplift_cycle_snapshots_new_assignments_and_preserves_prior_math(self):
        self._verify_follow_up("uplift","35","20")

    def test_defuel_cycle_snapshots_new_assignments_and_preserves_prior_math(self):
        self._verify_follow_up("defuel","20","10")

    def _verify_follow_up(self,kind,required,actual):
            with self.subTest(kind=kind):
                self.prepare(); self.save(self.start+timedelta(minutes=10),**self.final_data()); self.off()
                service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
                original=NeoScorpionFuelingEvent.query.one().performance_snapshot.copy()
                at=self.start+timedelta(minutes=20)
                service.start_follow_up_fuel_cycle(self.gateway,self.dispatcher,self.assignment.id,kind,required,
                    self.fueler.id,self.truck.id,now_utc=at,expected_cycle=1,expected_tail=self.mission.assigned_tail_number)
                db.session.commit()
                if kind == "uplift":
                    self.assertIsNone(self.assignment.fueler_assigned_at_utc)
                    from flask_login import login_user
                    class Clock(datetime):
                        @classmethod
                        def utcnow(cls): return at
                    with self.fixture.app.test_request_context(), patch.object(service,"datetime",Clock):
                        login_user(self.dispatcher)
                        service.save_dispatch_assignment(self.gateway,dict(mission_id=self.mission.id,
                            expected_assigned_fueler_user_id="",expected_assigned_truck_id="",
                            assigned_fueler_user_id=self.fueler.id,assigned_truck_id=self.truck.id))
                        db.session.commit()
                self.assertEqual(self.assignment.fueler_assigned_at_utc,at)
                self.assertEqual(self.assignment.truck_assigned_at_utc,at)
                previous_work=self.work
                self.assertEqual(previous_work.manual_on_at_utc,self.start)
                self.work=service._locked_current_fuel_work_state(self.assignment,self.mission)
                self.assertNotEqual(self.work.id,previous_work.id)
                self.assertIsNone(self.work.manual_on_at_utc)
                self.assertIsNone(self.work.actuals_received_at_utc)
                service.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,expected_cycle=2,
                    expected_tail=self.mission.assigned_tail_number,expected_truck_id=self.truck.id,now_utc=at)
                db.session.commit()
                self.save(at+timedelta(minutes=8),apu_running="no",actual_left=actual,actual_ctr="0",actual_right=actual,transfer_fuel_gallons="1493")
                self.off(at=at+timedelta(minutes=10))
                service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
                events=NeoScorpionFuelingEvent.query.order_by(NeoScorpionFuelingEvent.id).all()
                self.assertEqual(len(events),2)
                self.assertEqual(events[0].performance_snapshot,original)
                self.assertEqual(events[1].event_type,kind)
                self.assertEqual(events[1].cycle_number,2)
                self.assertEqual(Decimal(events[1].performance_snapshot["setup_allowance"]),8)
                self.assertEqual(events[1].started_at_utc,at)
                if kind == "defuel": self.assertEqual(self.selection.status,"needs_sump")

    def test_truck_swap_requires_new_physical_confirmation_and_snapshots_new_policy(self):
        self.prepare()
        self.save(self.start+timedelta(minutes=1),remaining_left="6",remaining_ctr="0",remaining_right="6",
                  actual_left="6",actual_ctr="0",actual_right="6",apu_running="no")
        replacement=self.fixture._nightly_truck("21"); db.session.commit()
        at=self.start+timedelta(minutes=2)
        service.swap_assignment_truck(self.gateway,self.dispatcher,self.assignment.id,replacement.id,now_utc=at)
        db.session.commit()
        original_work=self.work
        self.assertEqual(original_work.manual_on_at_utc,self.start)
        self.work=service._locked_current_fuel_work_state(self.assignment,self.mission)
        self.assertNotEqual(self.work.id,original_work.id)
        self.assertIsNone(self.work.manual_on_at_utc)
        self.assertEqual(self.assignment.truck_assigned_at_utc,at)
        with self.assertRaisesRegex(ValueError,"TRUCK ON"):
            self.off(at=at+timedelta(minutes=1))
        db.session.rollback()
        service.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,expected_cycle=1,
            expected_tail=self.mission.assigned_tail_number,expected_truck_id=replacement.id,now_utc=at+timedelta(minutes=1))
        db.session.commit()
        self.save(at+timedelta(minutes=10),**self.final_data()); self.off(at=at+timedelta(minutes=12))
        e=NeoScorpionFuelingEvent.query.one()
        self.assertEqual(e.fuel_truck_id,replacement.id)
        self.assertEqual(e.started_at_utc,at+timedelta(minutes=1))

    def test_cancel_followup_restores_original_pointer_without_rewriting_its_measurements(self):
        self.prepare(); self.save(self.start+timedelta(minutes=10),**self.final_data()); self.off()
        original=self.work
        original_event=NeoScorpionFuelingEvent.query.one()
        original_snapshot=dict(original_event.performance_snapshot)
        service.start_follow_up_fuel_cycle(self.gateway,self.dispatcher,self.assignment.id,"uplift","35",None,None,
            now_utc=self.start+timedelta(minutes=20),expected_cycle=1,expected_tail="N491UP")
        db.session.commit()
        cancelled=service._locked_current_fuel_work_state(self.assignment,self.mission)
        self.assertNotEqual(original.id,cancelled.id)
        service.cancel_uplift(self.gateway,self.dispatcher,self.assignment.id,expected_cycle=2,
                              expected_tail="N491UP",now_utc=self.start+timedelta(minutes=21))
        db.session.commit()
        self.assertEqual(self.assignment.active_work_state_id,original.id)
        self.assertEqual(original.physical_transfer_gallons,3000)
        self.assertEqual(original_event.performance_snapshot,original_snapshot)
        self.assertIsNotNone(cancelled.physical_finalized_at_utc)
        self.assertEqual(self.selection.current_gallons,7000)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)

    def test_fueler_off_and_actuals_require_dispatcher_complete_with_spear_enabled(self):
        self.prepare()
        self.settings.spear_automation_enabled=True
        self.settings.spear_learning_capture_enabled=True; db.session.commit()
        self.login(self.fueler)
        import json
        response=self.client.post("/neoscorpion/fueler/off",data={"assignment_id":self.assignment.id},follow_redirects=True)
        self.assertEqual(response.status_code,200)
        self.assertIn(b"TRUCK OFF RECORDED",response.data)
        self.assertNotIn(b"MISSION COMPLETED",response.data)
        self.assertIsNone(self.assignment.completed_at_utc)
        baseline=service.fueler_context(self.gateway,self.fueler)["rows"][0]["edit_baseline"]
        with patch("app.neonodes.neoscorpion.routes.capture_completed_learning_outcome") as capture:
            response=self.client.post(f"/neoscorpion/fueler/assignments/{self.assignment.id}",
                data={"expected":json.dumps(baseline),**self.final_data()},headers={"Accept":"application/json"})
            capture.assert_not_called()
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json["closed"])
        self.assertIsNone(self.assignment.completed_at_utc)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        form=dict(assignment_id=self.assignment.id,expected_work_ref=self.work.id)
        self.assertEqual(self.client.post("/neoscorpion/fuel-dispatch/complete",data=form).status_code,403)
        self.login(self.dispatcher)
        self.assertEqual(self.client.post("/neoscorpion/fuel-dispatch/complete",data=form).status_code,302)
        self.assertIsNotNone(self.assignment.completed_at_utc)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.assertEqual(self.client.post("/neoscorpion/fuel-dispatch/complete",data=form).status_code,302)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)

    def test_hold_invalid_data_and_legacy_on_cannot_force_completion_or_off(self):
        self.prepare()
        self.assignment.operational_status="hold_review"; db.session.commit()
        with self.assertRaisesRegex(ValueError,"REVIEW"):
            self.off()
        db.session.rollback(); self.assignment.operational_status="active"
        self.work.manual_on_at_utc=None; self.work.confirmed_on_at_utc=None
        self.work.on_at_utc=self.start; db.session.commit()
        with self.assertRaisesRegex(ValueError,"TRUCK ON"):
            self.off()
        db.session.rollback()
        self.assertIsNone(self.work.off_at_utc)

    def test_tail_acknowledgment_does_not_release_old_confirmed_physical_truck(self):
        self.prepare()
        self.save(self.start+timedelta(minutes=1), remaining_left="6", remaining_ctr="0", remaining_right="6", transfer_fuel_gallons="3000")
        old_baseline=service.fueler_context(self.gateway,self.fueler)["rows"][0]["edit_baseline"]
        original=self.work.manual_on_at_utc
        self.mission.assigned_tail_number="N492UP"; db.session.commit()
        service.confirm_assignment_tail(self.gateway,self.dispatcher,self.assignment.id,expected_cycle=1,expected_tail="N492UP")
        db.session.commit()
        self.assertEqual(self.work.manual_on_at_utc,original)
        self.assertIsNone(self.assignment.transfer_fuel_gallons)
        self.assertEqual(self.work.physical_transfer_gallons,3000)
        with self.assertRaises(service.FuelerDataConflict):
            service.mark_fueler_off(self.gateway,self.fueler,self.assignment.id,expected=old_baseline)
        db.session.rollback()
        with self.assertRaisesRegex(ValueError,"TRUCK STILL FUELING"):
            service.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,
                expected_cycle=1,expected_tail="N492UP",expected_truck_id=self.truck.id,now_utc=self.start+timedelta(minutes=1))
        db.session.rollback()
        self.assertIsNone(self.work.off_at_utc)
        rows=service.fuel_dispatch_context(self.gateway)["rows"]
        self.assertIn(self.truck.id,_current_resource_owners(rows)[1])
        source = next(row for row in service.fueler_context(self.gateway,self.fueler)["rows"] if row.get("is_source_segment"))
        self.assertEqual(source["tail_number"], "N491UP")
        identity = dict(physical_work_id=self.work.id, physical_sequence=self.work.physical_sequence_number)
        self.off(**identity)
        self.assertIsNone(self.assignment.completed_at_utc)
        self.assertFalse(self.selection.inventory_verified)
        self.assertEqual(service.fuel_dispatch_context(self.gateway)["rows"][0]["physical_segment_resources"], [])
        service.save_fueler_entry(self.gateway,self.fueler,
            dict(assignment_id=self.assignment.id, **identity, **self.final_data()),now_utc=self.start+timedelta(minutes=14))
        db.session.commit()
        self.assertIsNone(self.assignment.completed_at_utc)
        self.assertIsNone(self.assignment.transfer_fuel_gallons)
        event = NeoScorpionFuelingEvent.query.one()
        self.assertEqual(event.tail_number, "N491UP")
        self.assertEqual(event.transfer_fuel_gallons,3000)
        self.assertEqual(event.started_at_utc,original)
        self.assertEqual(self.selection.current_gallons,7000)
        self.off(**identity)
        service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id,**identity)
        db.session.commit()
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        service.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,
            expected_cycle=1,expected_tail="N492UP",expected_truck_id=self.truck.id,now_utc=self.start+timedelta(minutes=16))
        db.session.commit()
        self.save(self.start+timedelta(minutes=18),**{**self.final_data(),"transfer_fuel_gallons":"2000"})
        self.off(at=self.start+timedelta(minutes=25))
        events=NeoScorpionFuelingEvent.query.order_by(NeoScorpionFuelingEvent.id).all()
        self.assertEqual([(e.tail_number,e.transfer_fuel_gallons) for e in events],[("N491UP",3000)])
        service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        events=NeoScorpionFuelingEvent.query.order_by(NeoScorpionFuelingEvent.id).all()
        self.assertEqual([(e.tail_number,e.transfer_fuel_gallons) for e in events],[("N491UP",3000),("N492UP",2000)])
        self.assertEqual(self.selection.current_gallons,5000)
        self.assertIsNotNone(self.assignment.completed_at_utc)
        self.assertEqual(events[0].performance_snapshot["physical_work_id"],self.work.id)
        self.assertEqual(totals(load_jobs(self.gateway.id,operation_id=self.operation.id),"truck")["jobs"],2)

    def test_original_segment_end_early_preserves_replacement_and_posts_once(self):
        self.prepare(); self.save(self.start+timedelta(minutes=1),**self.final_data())
        self.mission.assigned_tail_number="N492UP"; db.session.commit()
        service.confirm_assignment_tail(self.gateway,self.dispatcher,self.assignment.id,expected_cycle=1,expected_tail="N492UP")
        identity=dict(physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number)
        result=service.end_fuel_work_early(self.gateway,self.dispatcher,self.assignment.id,"Original aircraft interrupted",**identity)
        db.session.commit()
        self.assertEqual(result.fueling_event.tail_number,"N491UP")
        self.assertIsNotNone(self.work.ended_early_at_utc)
        self.assertIsNone(self.assignment.completed_at_utc)
        self.assertEqual(self.assignment.operational_status,"active")
        self.assertEqual(self.selection.current_gallons,7000)

    def test_ambiguous_legacy_transfer_requires_explicit_segment_review(self):
        self.prepare(); self.save(self.start+timedelta(minutes=1),**self.final_data())
        self.work.physical_sequence_number=None
        self.work.physical_transfer_gallons=None
        self.mission.assigned_tail_number="N492UP"; db.session.commit()
        service.confirm_assignment_tail(self.gateway,self.dispatcher,self.assignment.id,expected_cycle=1,expected_tail="N492UP")
        db.session.commit()
        self.assertTrue(self.work.physical_transfer_review)
        self.assertIsNone(self.work.physical_transfer_gallons)
        identity=dict(physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number)
        self.off(**identity)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        with self.assertRaisesRegex(ValueError,"REVIEW REQUIRED"):
            service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id,**identity)
        db.session.rollback()
        with self.assertRaisesRegex(ValueError,"dispatcher must verify"):
            service.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,**identity,transfer_fuel_gallons="3000"))
        db.session.rollback()
        service.save_fueler_entry(self.gateway,self.dispatcher,dict(assignment_id=self.assignment.id,**identity,
            transfer_fuel_gallons="3000",segment_review_reason="Reviewed original truck transfer receipt"),dispatcher=True)
        db.session.commit()
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        submit_and_verify(self.gateway,self.dispatcher,self.assignment.id,**identity)
        db.session.commit()
        self.assertEqual(NeoScorpionFuelingEvent.query.one().tail_number,"N491UP")
        self.assertIsNone(self.assignment.completed_at_utc)

    def test_returning_tail_cannot_overwrite_its_finalized_physical_work(self):
        self._exercise_returning_tail()

    def test_returning_tail_with_different_truck_preserves_each_visit(self):
        self._exercise_returning_tail(different_truck=True)

    def _exercise_returning_tail(self,different_truck=False):
        self.prepare(); self.save(self.start+timedelta(minutes=1),**self.final_data()); self.off()
        submit_and_verify(self.gateway,self.dispatcher,self.assignment.id,
            now_utc=self.start+timedelta(minutes=12)); db.session.commit()
        original_id, original_sequence = self.work.id, self.work.physical_sequence_number
        original_on, original_off = self.work.manual_on_at_utc, self.work.off_at_utc
        self.mission.assigned_tail_number="N492UP"; db.session.commit()
        service.confirm_assignment_tail(self.gateway,self.dispatcher,self.assignment.id,expected_cycle=1,expected_tail="N492UP")
        service.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,expected_cycle=1,
            expected_tail="N492UP",expected_truck_id=self.truck.id,now_utc=self.start+timedelta(minutes=16))
        b_work=service._locked_current_fuel_work_state(self.assignment,self.mission)
        b_identity=dict(physical_work_id=b_work.id,physical_sequence=b_work.physical_sequence_number)
        self.save(self.start+timedelta(minutes=17),remaining_left="7",remaining_ctr="0",remaining_right="7",
                  transfer_fuel_gallons="1000")
        db.session.commit()
        self.mission.assigned_tail_number="N491UP"; db.session.commit()
        service.confirm_assignment_tail(self.gateway,self.dispatcher,self.assignment.id,expected_cycle=1,expected_tail="N491UP")
        db.session.commit()
        new_a=service._locked_current_fuel_work_state(self.assignment,self.mission)
        self.assertEqual(len({original_id,b_work.id,new_a.id}),3)
        self.assertIsNone(new_a.off_at_utc)
        self.assertIsNone(new_a.physical_transfer_gallons)
        self.assertIsNone(self.assignment.completed_at_utc)
        with self.assertRaisesRegex(ValueError,"TRUCK STILL FUELING"):
            service.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,expected_cycle=1,
                expected_tail="N491UP",expected_truck_id=self.truck.id,now_utc=self.start+timedelta(minutes=18))
        db.session.rollback()
        self.off(at=self.start+timedelta(minutes=19),**b_identity)
        self.assertIsNone(b_work.physical_finalized_at_utc)
        current_truck=self.truck
        if different_truck:
            current_truck=self.fixture._nightly_truck("21"); db.session.commit()
            service.swap_assignment_truck(self.gateway,self.dispatcher,self.assignment.id,current_truck.id,
                                          now_utc=self.start+timedelta(minutes=19))
            db.session.commit()
            new_a=service._locked_current_fuel_work_state(self.assignment,self.mission)
        service.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,expected_cycle=1,
            expected_tail="N491UP",expected_truck_id=current_truck.id,now_utc=self.start+timedelta(minutes=20))
        db.session.commit()
        self.save(self.start+timedelta(minutes=22),
            **{**self.final_data(),"actual_left":"14","actual_right":"14","transfer_fuel_gallons":"2000"})
        self.off(at=self.start+timedelta(minutes=30))
        self.assertIsNone(self.assignment.completed_at_utc)
        submit_and_verify(self.gateway,self.dispatcher,self.assignment.id,
            now_utc=self.start+timedelta(minutes=30)); db.session.commit()
        completion=self.assignment.completed_at_utc
        self.assertIsNotNone(completion)
        self.assertEqual(new_a.manual_on_at_utc,self.start+timedelta(minutes=20))
        self.assertEqual(new_a.off_at_utc,self.start+timedelta(minutes=30))
        self.assertEqual(new_a.physical_transfer_gallons,2000)
        self.assertFalse(self.selection.inventory_verified)  # B is still unresolved.
        service.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,**b_identity,
            apu_running="no",actual_left="10",actual_ctr="0",actual_right="10"),
            now_utc=self.start+timedelta(minutes=31))
        db.session.commit()
        submit_and_verify(self.gateway,self.dispatcher,self.assignment.id,**b_identity);db.session.commit()
        original=db.session.get(NeoScorpionFuelWorkState,original_id)
        self.assertEqual(original.physical_sequence_number,original_sequence)
        self.assertEqual(original.physical_transfer_gallons,3000)
        self.assertEqual((original.manual_on_at_utc,original.off_at_utc),(original_on,original_off))
        self.assertEqual(self.assignment.completed_at_utc,completion)
        events=NeoScorpionFuelingEvent.query.order_by(NeoScorpionFuelingEvent.id).all()
        self.assertEqual([(e.fuel_work_state_id,e.tail_number,e.transfer_fuel_gallons) for e in events],
            [(original_id,"N491UP",3000),(new_a.id,"N491UP",2000),(b_work.id,"N492UP",1000)])
        self.assertEqual(self.selection.current_gallons,6000 if different_truck else 4000)
        record=build_completed_learning_record(self.gateway,self.operation,self.assignment,self.mission)
        _validate_learning_record(record)
        self.assertEqual({item["work_state_id"] for item in record["fuel_events"]},{original_id,b_work.id,new_a.id})
        if not different_truck:
            # These v4 fixtures deliberately lack staging/accessibility proof.
            # Physical history is valid, but cannot become qualified training.
            self.assertFalse(record["training_eligible"])
            self.assertIn("unverified_resource_availability",record["exclusion_reasons"])
        if different_truck:
            self.assertEqual(NeoScorpionSortTruck.query.filter_by(fuel_truck_id=current_truck.id).one().current_gallons,8000)
        self.assertTrue(self.selection.inventory_verified)
        jobs=load_jobs(self.gateway.id,operation_id=self.operation.id)
        self.assertEqual(len(jobs),3)
        self.assertEqual(totals(jobs,"truck")["jobs"],0)
        self.assertTrue(all(job['truck_reasons'] for job in jobs))
        self.assertEqual({j["event"].fuel_work_state_id for j in load_observation_jobs(self.gateway.id,operation_id=self.operation.id)},
                         {original_id,b_work.id,new_a.id})
        for work in (original,new_a,b_work):
            submit_and_verify(self.gateway,self.dispatcher,self.assignment.id,
                physical_work_id=work.id,physical_sequence=work.physical_sequence_number)
        db.session.commit()
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),3)
        self.assertEqual(self.selection.current_gallons,6000 if different_truck else 4000)

    def test_same_tail_pending_actuals_survive_a_new_visit_and_stale_form(self):
        self.prepare(); self.save(self.start+timedelta(minutes=1),remaining_left="6",remaining_ctr="0",
            remaining_right="6",transfer_fuel_gallons="3000"); self.off()
        old=self.work
        baseline=service.fueler_context(self.gateway,self.fueler)["rows"][0]["edit_baseline"]
        service.begin_physical_visit(self.gateway,self.fueler,self.assignment.id,expected=baseline,separate_visit_confirmed=True,
                                    now_utc=self.start+timedelta(minutes=13))
        db.session.commit()
        new=service._locked_current_fuel_work_state(self.assignment,self.mission)
        self.assertNotEqual(old.id,new.id)
        self.assertIsNone(new.physical_transfer_gallons)
        self.login(self.dispatcher)
        response=self.client.post("/neoscorpion/fuel-dispatch/reopen-off",data={
            "assignment_id":self.assignment.id,"expected_work_ref":old.id,"reopen_reason":"Stale visit"})
        self.assertEqual(response.status_code,400)
        self.assertIsNone(new.off_at_utc)
        with self.assertRaises(service.FuelerDataConflict):
            service.mark_fueler_off(self.gateway,self.fueler,self.assignment.id,expected=baseline)
        db.session.rollback()
        service.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,expected_cycle=1,
            expected_tail="N491UP",expected_truck_id=self.truck.id,now_utc=self.start+timedelta(minutes=14))
        db.session.commit()
        rows=service.fueler_context(self.gateway,self.fueler)["rows"]
        self.assertEqual({r["physical_work_id"] for r in rows},{old.id,new.id})
        service.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,
            physical_work_id=old.id,physical_sequence=old.physical_sequence_number,
            apu_running="no",actual_left="15",actual_ctr="0",actual_right="15"),now_utc=self.start+timedelta(minutes=15))
        db.session.commit()
        self.assertIsNone(self.assignment.completed_at_utc)
        self.assertIsNone(new.off_at_utc)
        submit_and_verify(self.gateway,self.dispatcher,self.assignment.id,
            physical_work_id=old.id,physical_sequence=old.physical_sequence_number);db.session.commit()
        self.assertEqual(NeoScorpionFuelingEvent.query.one().fuel_work_state_id,old.id)
        self.assertEqual(old.physical_transfer_gallons,3000)

    def test_finalized_visit_does_not_satisfy_an_inconsistent_new_mission_state(self):
        self.prepare(); self.save(self.start+timedelta(minutes=1),**self.final_data()); self.off()
        original=self.work
        self.assignment.completed_at_utc=None
        self.assignment.review_status="assigned"
        self.mission.fuel_status="assigned"
        self.mission.fuel_completed_at_utc=None
        db.session.commit()
        self.assertFalse(service.fueler_context(self.gateway,self.fueler)["rows"][0]["administratively_complete"])
        with self.assertRaisesRegex(ValueError,"already finalized"):
            service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id)
        db.session.rollback()
        baseline=service.fueler_context(self.gateway,self.fueler)["rows"][0]["edit_baseline"]
        service.begin_physical_visit(self.gateway,self.fueler,self.assignment.id,expected=baseline,separate_visit_confirmed=True)
        db.session.commit()
        self.assertNotEqual(self.assignment.active_work_state_id,original.id)
        self.assertIsNone(self.assignment.transfer_fuel_gallons)
        self.assertEqual(original.physical_transfer_gallons,3000)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)

    def test_forecasts_share_policy_are_stable_and_stop_at_off(self):
        self.prepare(lead=5)
        self.save(self.start+timedelta(minutes=1), remaining_left="6",remaining_ctr="0",remaining_right="6",transfer_fuel_gallons="3000")
        row=service.fueler_context(self.gateway,self.fueler)["rows"][0]
        goal=row["physical_off_forecast"]["goal"]
        self.assertEqual(goal,self.start+timedelta(minutes=18))
        prediction=physical_off_forecast(row,service.assignment_planning_settings(self.gateway),settings=self.settings)
        self.assertEqual(prediction["likely"],goal)
        self.settings.assignment_setup_minutes=100; db.session.commit()
        self.assertEqual(service.fueler_context(self.gateway,self.fueler)["rows"][0]["physical_off_forecast"]["goal"],goal)
        self.off()
        self.assertIsNone(service.fueler_context(self.gateway,self.fueler)["rows"][0]["physical_off_forecast"]["likely"])

    def test_shared_prof_report_history_csv_permissions_and_no_n_plus_one(self):
        self.prepare(lead=5); self.save(self.start+timedelta(minutes=10),**self.final_data()); self.off()
        jobs=load_jobs(self.gateway.id,operation_id=self.operation.id)
        self.assertEqual(totals(jobs,"fueler")["prof"],150)
        self.assertEqual(totals(jobs,"truck")["prof"],83)
        self.settings.assignment_setup_minutes=100; db.session.commit()
        context=report_context(self.gateway,self.operation)
        self.assertEqual(context["summaries"]["fueler"]["prof"],150)
        self.assertIn("100 × (8.00 + 10.00)",context["copy_text"])
        self.login(self.dispatcher)
        response=self.client.get(f"/neoscorpion/reports/prof?operation_id={self.operation.id}&format=csv")
        self.assertEqual(response.status_code,200)
        self.assertIn(b"Fueler formula",response.data)
        self.assertIn(b"150",response.data)
        self.assertEqual(self.client.get(f"/neoscorpion/reports/prof?operation_id={self.operation.id}").status_code,200)
        self.assertEqual(self.client.get("/neoscorpion/reports/prof?operation_id=999999").status_code,404)
        statements=[]
        def capture(_c,_cu,sql,*_args): statements.append(sql)
        event.listen(db.engine,"before_cursor_execute",capture)
        try: load_jobs(self.gateway.id,operation_id=self.operation.id)
        finally: event.remove(db.engine,"before_cursor_execute",capture)
        self.assertEqual(len(statements),1)

    def test_calibration_service_duration_does_not_train_mechanical_pump_or_setup_policy(self):
        self.prepare(lead=5); self.save(self.start+timedelta(minutes=10),**self.final_data()); self.off()
        rows=service.fuel_dispatch_context(self.gateway)["rows"]
        calibration=build_live_calibration(self.operation,service.assignment_planning_settings(self.gateway),rows)
        self.assertNotIn(("pump_rate","B757"),calibration)
        self.assertNotIn(("setup_minutes","B757"),calibration)
        self.assertIn(("service_minutes","B757"),calibration)
        self.assertEqual(calibration[("actual_wrapup_minutes","B757")].observed,0)
        record=build_completed_learning_record(self.gateway,self.operation,self.assignment,self.mission)
        self.assertEqual(record["schema_version"],"spear-learning/v2")
        self.assertEqual(Decimal(record["operational_timelines"][0]["facts"]["setup_allowance"]),8)
        _validate_learning_record(record)

    def test_schema_upgrade_is_additive_idempotent_preserves_timestamps(self):
        self.prepare(); original=self.work.manual_on_at_utc
        sync_local_sqlite_schema(self.fixture.app); sync_local_sqlite_schema(self.fixture.app)
        self.assertIn("performance_snapshot",{c["name"] for c in inspect(db.engine).get_columns("neoscorpion_fueling_events")})
        self.assertEqual(self.work.manual_on_at_utc,original)

    def test_sqlite_legacy_identity_migration_preserves_event_and_tank_foreign_keys(self):
        self.prepare(); self.save(self.start+timedelta(minutes=1),**self.final_data()); self.off()
        service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        work_id=self.work.id
        event_id=NeoScorpionFuelingEvent.query.one().id
        db.session.execute(text("UPDATE neoscorpion_fuel_assignments SET active_work_state_id=NULL"))
        db.session.execute(text("UPDATE neoscorpion_fuel_work_states SET visit_number=NULL"))
        create_sql=db.session.execute(text("SELECT sql FROM sqlite_master WHERE name='neoscorpion_fuel_work_states'")).scalar()
        old_sql=create_sql.replace("uq_scorpion_work_assignment_visit UNIQUE (fuel_assignment_id, visit_number)",
                                  "uq_neoscorpion_fuel_work_state_assignment_tail UNIQUE (fuel_assignment_id, tail_number)")
        self.assertNotEqual(old_sql,create_sql)
        db.session.execute(text("PRAGMA legacy_alter_table=ON"))
        db.session.execute(text("ALTER TABLE neoscorpion_fuel_work_states RENAME TO fixture_work_legacy"))
        db.session.execute(text(old_sql))
        db.session.execute(text("INSERT INTO neoscorpion_fuel_work_states SELECT * FROM fixture_work_legacy"))
        db.session.execute(text("DROP TABLE fixture_work_legacy"))
        db.session.execute(text("PRAGMA legacy_alter_table=OFF")); db.session.commit()
        self.assertIn("uq_neoscorpion_fuel_work_state_assignment_tail",
            {c["name"] for c in inspect(db.engine).get_unique_constraints("neoscorpion_fuel_work_states")})
        sync_local_sqlite_schema(self.fixture.app); db.session.commit()
        sync_local_sqlite_schema(self.fixture.app); db.session.commit(); db.session.expire_all()
        self.assertEqual(self.assignment.active_work_state_id,work_id)
        self.assertEqual(db.session.get(NeoScorpionFuelingEvent,event_id).fuel_work_state_id,work_id)
        self.assertEqual(self.work.physical_transfer_gallons,3000)
        self.assertEqual(next(t for t in self.work.tank_states if t.tank_code=="left").actual_lbs,15000)
        for table in ("neoscorpion_fueling_events","neoscorpion_fuel_tank_states"):
            foreigns=inspect(db.engine).get_foreign_keys(table)
            self.assertTrue(any(f["referred_table"]=="neoscorpion_fuel_work_states" for f in foreigns))
        self.assertEqual(db.session.execute(text("PRAGMA foreign_key_check")).all(),[])

    def test_concurrent_baseline_and_stale_cycle_guard(self):
        self.prepare()
        self.save(self.start+timedelta(minutes=1),remaining_left="6",remaining_ctr="0",remaining_right="6")
        baseline=service.fueler_context(self.gateway,self.fueler)["rows"][0]["edit_baseline"]
        self.off()
        self.save(self.start+timedelta(minutes=14),actual_left="15")
        import json
        with self.assertRaises(service.FuelerDataConflict):
            service.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,
                expected=json.dumps(baseline),actual_left="16"),require_expected=True)
        db.session.rollback()
        self.assertEqual(next(t for t in self.work.tank_states if t.tank_code == "left").actual_lbs,15000)


class ForecastMathTest(unittest.TestCase):
    def test_spear_uses_prior_visits_not_current_segment_in_the_same_cycle(self):
        at=datetime(2026,10,14,4,55)
        assignment=SimpleNamespace(id=9,current_cycle_number=1,assigned_fueler_user_id=1,assigned_truck_id=2,transfer_fuel_gallons=3000)
        work=SimpleNamespace(id=104,off_at_utc=None,ended_early_at_utc=None,performance_context={"pump_rate":"300","setup_allowance":"8"})
        row=dict(assignment=assignment,fuel_work_state=work,detailed_aircraft_type="B757",fueling_effective_on_utc=at)
        planning=SimpleNamespace(setup_minutes=8,pump_rate_for=lambda _:Decimal(300))
        jobs=[dict(event=SimpleNamespace(fuel_assignment_id=9,cycle_number=1,fuel_work_state_id=i,fueler_user_id=1,fuel_truck_id=2),
            facts={"aircraft_type":"B757"},fueler_reasons=(),expected_fueler=Decimal(18),actual_minutes=Decimal(27)) for i in (101,102,103,104)]
        forecast=physical_off_forecast(row,planning,jobs=jobs,mode="apply")
        self.assertEqual(forecast["samples"],3)
        self.assertGreater(forecast["likely"],forecast["goal"])

    def test_physical_seconds_across_midnight_and_dst_do_not_use_local_clock_subtraction(self):
        facts=dict(version=3,aircraft_type="B757",on_source="manual",fueler_id=1,truck_id=2,
            pump_rate="300",setup_allowance="0",fueler_assigned_at="2026-11-01T06:00:00",truck_assigned_at="2026-11-01T06:30:00",
            actuals_received_at="2026-11-01T07:07:00",completed_at="2026-11-01T07:08:00",exclusions=[])
        e=SimpleNamespace(performance_snapshot=facts,started_at_utc=datetime(2026,11,1,6,55),ended_at_utc=datetime(2026,11,1,7,5),
            fueler_user_id=1,fuel_truck_id=2,transfer_fuel_gallons=3000,event_type="fuel")
        result=event_math(e)
        self.assertEqual((result["actual_minutes"],result["wrap_minutes"],result["admin_minutes"]),(10,2,1))
        self.assertEqual((result["fueler_prof"],result["truck_prof"]),(100,100))

    def test_apply_unique_weighted_samples_observe_baseline_and_midnight(self):
        start=datetime(2026,10,14,4,55)
        assignment=SimpleNamespace(id=9,current_cycle_number=1,assigned_fueler_user_id=1,assigned_truck_id=2,transfer_fuel_gallons=3000)
        work=SimpleNamespace(off_at_utc=None,ended_early_at_utc=None,performance_context={"pump_rate":"300","setup_allowance":"8"})
        row=dict(assignment=assignment,fuel_work_state=work,detailed_aircraft_type="B757",fueling_effective_on_utc=start)
        planning=SimpleNamespace(setup_minutes=8,pump_rate_for=lambda _:Decimal(300))
        jobs=[dict(event=SimpleNamespace(fuel_assignment_id=i,cycle_number=1,fueler_user_id=1,fuel_truck_id=2),
                   facts={"aircraft_type":"B757"},fueler_reasons=(),expected_fueler=Decimal(18),actual_minutes=Decimal(27)) for i in range(3)]
        observe=physical_off_forecast(row,planning,jobs=jobs,mode="observe")
        apply=physical_off_forecast(row,planning,jobs=jobs,mode="apply")
        self.assertEqual(observe["goal"],start+timedelta(minutes=18))
        self.assertEqual(observe["likely"],observe["goal"])
        self.assertEqual(apply["difference"],7)
        self.assertEqual(apply["samples"],3)
        self.assertEqual(physical_off_forecast(row,planning,jobs=jobs[:2],mode="apply")["source"],"BASELINE")
        self.assertEqual(apply["goal"].date(),start.date())
