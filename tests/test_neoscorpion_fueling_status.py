"""Shared progress, independent urgency, verified FOB and dispatcher advisory."""
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import event, inspect

from app.extensions import db
from app.models import (NeoScorpionCallDispatchAck, NeoScorpionFuelTankState, NeoScorpionFuelWorkState,
    NeoScorpionSettings, NeoScorpionSortAssetState, NeoScorpionFuelingEvent, SortDateMission,
    SortDateParkingAssignment)
from app.services import neoscorpion as service
from app.services.neoscorpion_fueling_status import fueling_status, fob_assessment, status_color, STAGE_COLORS
from app.services.neoscorpion_call_dispatch import call_dispatch_alert
from app.services.schema_sync import sync_local_sqlite_schema
from tests import test_neoscorpion_dispatch_planning as fixture_module
from tests import test_neoscorpion_fuel_interruptions as interruption_fixture


class FuelingStatusClassificationTest(unittest.TestCase):
    def test_pending_secondary_is_empty_but_missing_reasons_remain_internal(self):
        row=self.row(); row.update(arrival_status='Scheduled',inbound_fuel_lbs=None,required_fuel_lbs=None,parking_valid=False)
        status=fueling_status(row)
        self.assertEqual(status['dispatch_status_label'],'PENDING')
        self.assertEqual(status['dispatch_status_detail'],'Needs Arrival, Inbound, Required, Parking')
        self.assertEqual(status['fuel_status_secondary'],'')

    def test_one_secondary_prioritizes_timing_and_discrepancy_over_routine_details(self):
        row=self.row(); row.update(tail_mismatch=True,direction_mismatch=True,fob_likely=True)
        status=fueling_status(row)
        self.assertEqual(status['fuel_status_secondary'],'Fuel direction discrepancy')
        self.assertEqual(len(status['fuel_status_warnings']),3)
        row['fuel_work_state'].on_at_utc=datetime(2026,10,9,2)
        status=fueling_status(row)
        self.assertEqual(status['fuel_status_secondary'],'TIMING UNKNOWN')

    def test_macro_call_dispatch_uses_only_secondary_line_and_preserves_fueler_secrecy(self):
        from app import create_app
        from flask import render_template_string
        row=self.row(); row.update(fob_likely=True,cycle_type='fuel',cycle_number=1,
            call_dispatch_alert=SimpleNamespace(fingerprint='abc'),tail_mismatch=True)
        row['mission'].id=1; row['mission'].flight_number='UPS1'
        row.update(fueling_status(row))
        app=create_app(type('Config',(),dict(SECRET_KEY='test',TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',SQLALCHEMY_TRACK_MODIFICATIONS=False)))
        source="{% from 'neonodes/neoscorpion/_fuel_status.html' import fuel_status with context %}{{ fuel_status(row, dispatcher=dispatcher, can_ack=dispatcher) }}"
        with app.test_request_context('/'):
            html=render_template_string(source,row=row,dispatcher=True)
            self.assertEqual(html.count('data-fuel-status-secondary'),1)
            self.assertIn('CALL DISPATCH',html); self.assertNotIn('>ACK</button>',html)
            ack=render_template_string("{% from 'neonodes/neoscorpion/_fuel_status.html' import call_dispatch_ack with context %}{{ call_dispatch_ack(row) }}",row=row)
            self.assertIn('>ACK</button>',ack); self.assertIn('alert_fingerprint',ack)
            self.assertNotIn('FOB LIKELY',html); self.assertNotIn('TAIL SWAP',html)
            fueler=render_template_string(source,row=row,dispatcher=False)
            self.assertEqual(fueler.count('data-fuel-status-secondary'),1)
            self.assertNotIn('CALL DISPATCH',fueler); self.assertNotIn('>ACK</button>',fueler)
            self.assertIn('FOB LIKELY',fueler)

    def row(self):
        return dict(mission=SimpleNamespace(eta_datetime_utc=None, planned_datetime_utc=datetime(2026, 10, 9, 8)),
            assignment=SimpleNamespace(assigned_fueler_user_id=None, assigned_truck_id=None, review_status="pending"),
            fuel_work_state=SimpleNamespace(on_at_utc=None, off_at_utc=None), arrival_status="On Ground",
            inbound_fuel_lbs=12000, required_fuel_lbs=25000, parking_valid=True)

    def test_all_primary_transitions_and_partial_assignments(self):
        row = self.row()
        stages = []
        row["parking_valid"] = False
        stages.append(fueling_status(row)["dispatch_status_key"])
        row["parking_valid"] = True
        stages.append(fueling_status(row)["dispatch_status_key"])
        for fueler, truck, detail in ((1, None, "Needs Truck"), (None, 2, "Needs Fueler")):
            row["assignment"].assigned_fueler_user_id, row["assignment"].assigned_truck_id = fueler, truck
            self.assertEqual(fueling_status(row)["dispatch_status_key"], "ready")
            self.assertEqual(fueling_status(row)["dispatch_status_detail"], detail)
        row["assignment"].assigned_fueler_user_id = 1
        row["assignment"].ready_for_fuel_at_utc = datetime(2026, 10, 9, 1)
        stages.append(fueling_status(row)["dispatch_status_key"])
        row["fuel_work_state"].on_at_utc = datetime(2026, 10, 9, 2)
        stages.append(fueling_status(row)["dispatch_status_key"])
        row["fuel_work_state"].off_at_utc = datetime(2026, 10, 9, 3)
        stages.append(fueling_status(row)["dispatch_status_key"])
        row["administratively_complete"] = True
        stages.append(fueling_status(row)["dispatch_status_key"])
        row["fuel_on_board_complete"] = True
        stages.append(fueling_status(row)["dispatch_status_key"])
        self.assertEqual(stages, ["pending", "ready", "assigned", "fueling", "off", "complete", "fob"])

    def test_fob_check_transitions_do_not_require_truck_or_off(self):
        row = self.row()
        row["fob_likely"] = True
        self.assertIn("FOB LIKELY", fueling_status(row)["fuel_status_warnings"])
        row["assignment"].assigned_fueler_user_id = 1
        self.assertEqual(fueling_status(row)["dispatch_status_key"], "assigned")
        self.assertIn("FOB CHECK", fueling_status(row)["fuel_status_warnings"])
        row["fuel_work_state"].on_at_utc = datetime(2026, 10, 9, 2)
        self.assertEqual(fueling_status(row)["dispatch_status_key"], "fueling")
        row["fuel_on_board_ready"] = True
        self.assertEqual(fueling_status(row)["dispatch_status_key"], "fob-ready")

    def test_started_work_wins_missing_data_and_resources_tail_swap_is_advisory(self):
        row = self.row()
        row.update(inbound_fuel_lbs=None, required_fuel_lbs=None, parking_valid=False,
                   tail_mismatch=True, direction_mismatch=True)
        row["assignment"].review_status = "review"
        row["fuel_work_state"].on_at_utc = datetime(2026, 10, 9, 2)
        result = fueling_status(row)
        self.assertEqual(result["dispatch_status_key"], "fueling")
        self.assertNotIn("Needs", result["dispatch_status_detail"])
        self.assertIn("TAIL SWAP · advisory", result["fuel_status_warnings"])
        self.assertIn("Fuel direction discrepancy", result["fuel_status_warnings"])
        for blocker in ("effective_hold", "work_ended_early"):
            with self.subTest(blocker=blocker):
                held = dict(row, **{blocker: True})
                self.assertEqual(fueling_status(held)["dispatch_status_label"], "REVIEW")
        row["administratively_complete"] = True
        row["effective_hold"] = True
        self.assertEqual(fueling_status(row)["dispatch_status_key"], "complete")

    def test_basic_missing_fields_arrival_formats_and_measured_inbound(self):
        for key, value, label in (("arrival_status", "Scheduled", "Arrival"), ("inbound_fuel_lbs", None, "Inbound"),
                ("required_fuel_lbs", None, "Required"), ("parking_valid", False, "Parking")):
            with self.subTest(key=key):
                row = self.row(); row[key] = value
                status = fueling_status(row)
                self.assertEqual(status["dispatch_status_key"], "pending")
                self.assertIn(label, status["dispatch_status_detail"])
        for arrived in ("On Ground", "Arrived"):
            row = self.row(); row.update(arrival_status=arrived, inbound_fuel_lbs=None, measured_inbound_fuel_lbs=0)
            self.assertEqual(fueling_status(row)["dispatch_status_key"], "ready")

    def test_time_urgency_every_stage_including_past_and_exact_boundary(self):
        now = datetime(2026, 10, 9, 3)
        for stage, base in STAGE_COLORS.items():
            with self.subTest(stage=stage):
                self.assertEqual(status_color(stage, now+timedelta(minutes=31), now=now), base)
                expected = "red" if stage in {"pending", "ready", "assigned", "off", "fob-ready", "review"} else base
                for etd in (now+timedelta(minutes=30), now-timedelta(minutes=5)):
                    self.assertEqual(status_color(stage, etd, now=now), expected)
        self.assertEqual(status_color("assigned", now+timedelta(minutes=10), now=now, threshold=5), "orange")
        self.assertEqual(status_color("complete", None, now=now), "green")

    def test_fueling_only_red_for_spear_risk_unknown_remains_yellow(self):
        now = datetime(2026, 10, 9, 3)
        etd = now + timedelta(minutes=40)
        for finish, expected in ((None, "yellow"), (etd-timedelta(minutes=20), "yellow"),
                (etd-timedelta(minutes=19), "red"), (etd+timedelta(minutes=1), "red")):
            self.assertEqual(status_color("fueling", etd, now=now, predicted_finish=finish), expected)
        row = self.row(); row["fuel_work_state"].on_at_utc = now
        self.assertIn("TIMING UNKNOWN", fueling_status(row)["fuel_status_warnings"])
        self.assertEqual(status_color("fueling", now-timedelta(minutes=10), now=now), "yellow")

    def test_fob_estimates_never_verify_and_measurements_take_precedence(self):
        args = dict(required=25000, inbound=30000, remaining=None, actual=None, apu_allowance=500,
                    apu_confirmed=False, apu_source_valid=True)
        assessed = fob_assessment(**args)
        self.assertTrue(assessed["fob_likely"]); self.assertFalse(assessed["fob_verified"])
        for changes in ({"remaining": 24000}, {"actual": 24000, "apu_confirmed": True},
                        {"transfer": 1}, {"cycle_type": "defuel"}):
            with self.subTest(changes=changes):
                self.assertFalse(fob_assessment(**dict(args, **changes))["fob_likely"])
        self.assertTrue(fob_assessment(**dict(args, actual=25500, apu_confirmed=True))["fob_verified"])
        self.assertTrue(fob_assessment(**dict(args, inherited=25500))["fob_verified"])
        self.assertFalse(fob_assessment(**dict(args, inherited=25499))["fob_verified"])
        self.assertFalse(fob_assessment(**dict(args, actual=30000, apu_confirmed=False))["fob_verified"])
        self.assertFalse(fob_assessment(**dict(args, apu_allowance=None))["fob_likely"])


class FuelingStatusIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.NeoScorpionDispatchPlanningTest()
        self.fixture.setUp(); self.addCleanup(self.fixture.tearDown)
        self.gateway, self.operation, self.client = self.fixture.gateway, self.fixture.operation, self.fixture.client
        self.dispatcher = self.fixture._login_user("status-dispatcher", "simulator")
        self.fueler = self.fixture._login_user("status-fueler", "operator")
        self.mission = self.fixture._mission("UPS901", "N491UP", 25000, 1)
        self.mission.planned_datetime_utc = datetime.utcnow() + timedelta(hours=2)
        self.arrival = SortDateMission(sort_date_operation_id=self.operation.id, sort_date=self.operation.sort_date,
            gateway_code=self.gateway.code, sort_name="night", mission_type="arrival", mission_source="manual",
            flight_number="UPS801", origin="SDF", destination=self.gateway.code, assigned_tail_number="N491UP", arrival_status="on_ground")
        db.session.add_all([self.arrival, SortDateParkingAssignment(sort_date_operation_id=self.operation.id,
            tail_number="N491UP", ramp_code="B", position_code="B06", lane_number=1)])
        self.tail = self.fixture._tail_fuel(self.mission, inbound_lbs=30000)
        self.assignment = self.fixture._assignment(self.mission)
        self.settings = NeoScorpionSettings(gateway_id=self.gateway.id)
        db.session.add(self.settings); db.session.commit()

    def row(self):
        db.session.commit()
        return next(row for row in service.fuel_dispatch_context(self.gateway)["rows"] if row["mission"].id == self.mission.id)

    def login(self, user):
        self.client.post('/logout')
        self.client.post('/login', data={"email":user.email,"password":"TestPassword123!"})

    def work(self, remaining=30000, actual=None, apu=False, allowance=0):
        work = NeoScorpionFuelWorkState(fuel_assignment_id=self.assignment.id, tail_number="N491UP",
            apu_running=apu, apu_allowance_lbs=allowance, automatic_apu_allowance_lbs=allowance,
            apu_source_tank_code="left" if apu else None)
        db.session.add(work); db.session.flush()
        for code, share in (("left", 1), ("ctr", 0), ("right", 0)):
            db.session.add(NeoScorpionFuelTankState(fuel_work_state_id=work.id, tank_code=code,
                remaining_lbs=remaining*share if remaining is not None else None,
                actual_lbs=actual*share if actual is not None else None))
        db.session.commit()
        return work

    def test_unknown_apu_recommended_and_manual_override_change_fob_only(self):
        now = datetime.utcnow()
        expected = service.calculate_apu_allowance_lbs(self.mission.planned_datetime_utc, self.operation.window_minutes,
                                                     now, Decimal("0.30"))
        row = service._fuel_rows(self.operation, [self.mission], now_utc=now)[0]
        self.assertEqual(row["fob_projected_neo_lbs"], 30000-expected)
        self.assertTrue(row["fob_likely"]); self.assertFalse(row["fuel_on_board_ready"])
        self.assignment.assigned_fueler_user_id = self.fueler.id
        work = self.work(actual=30000, apu=True, allowance=500)
        work.apu_override_enabled = True; work.apu_override_allowance_lbs = 6000
        row = self.row()
        self.assertFalse(row["fob_likely"]); self.assertFalse(row["fuel_on_board_ready"])
        self.assertEqual(row["fob_projected_neo_lbs"], 24000)
        self.assertEqual(row["estimated_fuel_gallons"], 0)
        work.apu_override_allowance_lbs = 500
        self.assertTrue(self.row()["fob_likely"])

    def test_remaining_then_actual_correct_estimate_and_truckless_fob_completion(self):
        self.assignment.assigned_fueler_user_id = self.fueler.id
        self.assertEqual(self.row()["dispatch_status_key"], "assigned")
        work = self.work(remaining=24000, actual=None)
        self.assertFalse(self.row()["fob_likely"])
        for tank in work.tank_states:
            tank.remaining_lbs = 30000 if tank.tank_code == "left" else 0
        work.on_at_utc = datetime.utcnow()
        row = self.row()
        self.assertEqual(row["dispatch_status_key"], "fueling")
        self.assertIn("FOB CHECK", row["fuel_status_warnings"])
        for tank in work.tank_states: tank.actual_lbs = tank.remaining_lbs
        row = self.row()
        self.assertEqual(row["dispatch_status_key"], "fob-ready")
        self.assertIsNone(work.off_at_utc)
        result = service.complete_fuel_on_board(self.gateway, self.dispatcher, self.assignment.id)
        db.session.commit()
        self.assertTrue(result.changed)
        self.assertEqual(self.row()["dispatch_status_key"], "fob")
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)

    def test_insufficient_actual_blocks_fob_and_returns_normal_workflow(self):
        self.assignment.assigned_fueler_user_id = self.fueler.id
        self.work(remaining=24000, actual=24000)
        row = self.row()
        self.assertEqual(row["dispatch_status_key"], "ready")
        self.assertEqual(row["dispatch_status_detail"], "Needs Truck")
        self.assertFalse(row["fuel_on_board_ready"])
        with self.assertRaisesRegex(ValueError, "Insufficient verified"):
            service.complete_fuel_on_board(self.gateway, self.dispatcher, self.assignment.id)
        db.session.rollback()

    def test_confirmed_and_unconfirmed_actual_requires_complete_tanks_and_valid_apu(self):
        self.assignment.assigned_fueler_user_id = self.fueler.id
        work = self.work(actual=30000, apu=None, allowance=None)
        self.assertFalse(self.row()["fuel_on_board_ready"])
        work.apu_running = True; work.automatic_apu_allowance_lbs = 0
        self.assertFalse(self.row()["fuel_on_board_ready"])
        work.apu_source_tank_code = "left"
        self.assertTrue(self.row()["fuel_on_board_ready"])
        work.tank_states[0].actual_lbs = None
        self.assertFalse(self.row()["fuel_on_board_ready"])

    def test_spear_timing_known_risk_and_unknown_shared_with_fueler(self):
        self.assignment.assigned_fueler_user_id = self.fueler.id
        self.assignment.assigned_truck_id = self.fixture._nightly_truck().id
        self.mission.planned_fuel_load = 50000
        self.work(actual=None).on_at_utc = datetime.utcnow()
        row = self.row()
        self.assertEqual(row["fuel_status_color"], "yellow")
        self.assertIn("TIMING UNKNOWN", row["fuel_status_warnings"])
        self.arrival.actual_block_in_datetime_utc = datetime.utcnow()-timedelta(minutes=10)
        self.mission.eta_datetime_utc = datetime.utcnow()+timedelta(minutes=10)
        row = self.row()
        self.assertEqual(row["fuel_status_color"], "red")
        self.assertNotIn("TIMING UNKNOWN", row["fuel_status_warnings"])
        other = service.fueler_context(self.gateway, self.fueler)["rows"][0]
        self.assertEqual(other["fuel_status_color"], row["fuel_status_color"])
        self.assertEqual(other["dispatch_status_label"], "FUELING")

    def test_apply_timing_includes_completed_observations_in_all_views(self):
        self.assignment.assigned_fueler_user_id = self.fueler.id
        self.mission.planned_fuel_load = 50000
        self.arrival.actual_block_in_datetime_utc = datetime.utcnow()-timedelta(minutes=10)
        self.work(actual=None).on_at_utc = datetime.utcnow()
        completed = self.fixture._mission("UPS999", "N492UP", 20000, 1)
        source = self.fixture._assignment(completed)
        source.completed_at_utc = datetime.utcnow(); source.review_status = "complete"
        completed.fuel_status = "complete"
        source_work = NeoScorpionFuelWorkState(fuel_assignment_id=source.id,tail_number="N492UP")
        db.session.add(source_work); db.session.flush()
        source_truck = self.fixture._nightly_truck()
        for sequence in (1,2,3):
            db.session.add(NeoScorpionFuelingEvent(sort_date_operation_id=self.operation.id,
                fuel_assignment_id=source.id, fuel_work_state_id=source_work.id, tail_number="N492UP",
                fuel_truck_id=source_truck.id,
                sequence_number=sequence, started_at_utc=datetime.utcnow()-timedelta(minutes=30),
                ended_at_utc=datetime.utcnow()-timedelta(minutes=10), transfer_fuel_gallons=1000))
        observed = self.row()["fueling_predicted_finish_utc"]
        self.settings.spear_live_calibration_mode = "apply"
        dispatch = self.row()
        self.assertNotEqual(observed,dispatch["fueling_predicted_finish_utc"])
        fueler = service.fueler_context(self.gateway,self.fueler)["rows"][0]
        hanzo = next(r for r in service.hanzo_context(self.gateway)["rows"] if r["mission"].id==self.mission.id)
        self.assertEqual(dispatch["fueling_predicted_finish_utc"],fueler["fueling_predicted_finish_utc"])
        self.assertEqual(dispatch["fueling_predicted_finish_utc"],hanzo["fueling_predicted_finish_utc"])

    def test_call_dispatch_ack_audit_persistence_realert_and_stale_rejection(self):
        self.login(self.dispatcher)
        row = self.row(); alert = row["call_dispatch_alert"]
        self.assertIsNotNone(alert)
        form = dict(mission_id=self.mission.id, expected_cycle=1, alert_fingerprint=alert["fingerprint"])
        response = self.client.post('/neoscorpion/fuel-dispatch/call-dispatch/ack', data=form)
        self.assertEqual(response.status_code, 200); self.assertTrue(response.json["changed"])
        self.assertIsNone(self.row()["call_dispatch_alert"])
        ack = NeoScorpionCallDispatchAck.query.one()
        self.assertEqual(ack.actor_user_id, self.dispatcher.id)
        self.assertEqual(ack.alert_values["available_lbs"], 30000)
        self.assertIsNotNone(ack.acknowledged_at_utc)
        self.assertFalse(self.client.post('/neoscorpion/fuel-dispatch/call-dispatch/ack', data=form).json["changed"])
        self.mission.planned_fuel_load = 24900; db.session.commit()
        self.assertIsNotNone(self.row()["call_dispatch_alert"])
        self.assertEqual(self.client.post('/neoscorpion/fuel-dispatch/call-dispatch/ack', data=form).status_code, 409)
        self.assertEqual(NeoScorpionCallDispatchAck.query.count(), 1)
        self.assignment.current_cycle_number = 2
        form.update(expected_cycle=2, alert_fingerprint=self.row()["call_dispatch_alert"]["fingerprint"])
        self.assertTrue(self.client.post('/neoscorpion/fuel-dispatch/call-dispatch/ack', data=form).json["changed"])
        self.assertEqual({a.cycle_number for a in NeoScorpionCallDispatchAck.query.all()}, {1,2})

    def test_physical_save_on_off_complete_preserves_transfer_and_inventory(self):
        truck = self.fixture._nightly_truck()
        self.assignment.assigned_fueler_user_id = self.fueler.id
        self.assignment.assigned_truck_id = truck.id
        db.session.commit()
        self.assertEqual(self.row()["dispatch_status_key"],"assigned")
        service.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,
            apu_running="no",remaining_left="6",remaining_ctr="0",remaining_right="6",
            actual_left="15",actual_ctr="0",actual_right="15",transfer_fuel_gallons="2687",notes=""))
        db.session.commit()
        self.assertEqual(self.row()["dispatch_status_key"],"fueling")
        self.assertIsNone(self.row()["call_dispatch_alert"])
        service.mark_fueler_off(self.gateway,self.fueler,self.assignment.id)
        db.session.commit()
        self.assertEqual(self.row()["dispatch_status_key"],"off")
        service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id)
        db.session.commit()
        self.assertEqual(self.row()["dispatch_status_key"],"complete")
        self.assertEqual(self.row()["fuel_status_color"],"green")
        self.assertEqual(self.assignment.transfer_fuel_gallons,2687)
        self.assertEqual(NeoScorpionFuelingEvent.query.one().transfer_fuel_gallons,2687)
        self.assertEqual(service.fuel_dispatch_context(self.gateway)["nightly_trucks"][0]["selection"].current_gallons,7313)

    def test_changed_fuel_apu_density_threshold_realert_inclusive_boundaries(self):
        self.assignment.assigned_fueler_user_id = self.fueler.id
        work = self.work(remaining=30000, actual=30000)
        row = self.row()
        row["fob_projected_neo_lbs"] = 28350
        self.assertIsNotNone(call_dispatch_alert(row, self.settings))
        row["fob_projected_neo_lbs"] = 28349
        self.assertIsNone(call_dispatch_alert(row, self.settings))
        baseline = self.row()["call_dispatch_alert"]["fingerprint"]
        for key, value in (("neo_fuel_excess_alert_gallons", 501), ("fuel_density_lbs_per_gallon", 7)):
            setattr(self.settings, key, value)
            self.assertNotEqual(self.row()["call_dispatch_alert"]["fingerprint"], baseline)
        work.apu_running = True; work.apu_source_tank_code = "left"
        work.apu_override_enabled = True; work.apu_override_allowance_lbs = 100
        self.assertNotEqual(self.row()["call_dispatch_alert"]["fingerprint"], baseline)

    def test_dispatcher_only_warning_permissions_and_live_fragments(self):
        self.assignment.assigned_fueler_user_id = self.fueler.id
        self.work(remaining=30000, actual=None)
        self.login(self.dispatcher)
        html = self.client.get('/neoscorpion/fuel-dispatch/live-panel').json["html"]
        self.assertIn("CALL DISPATCH", html); self.assertIn('data-fuel-status="assigned"', html)
        self.login(self.fueler)
        self.assertEqual(self.client.post('/neoscorpion/fuel-dispatch/call-dispatch/ack', data={}).status_code, 403)
        for url in ('/neoscorpion/fueler','/neoscorpion/fueling-board','/neoscorpion/fuel-dispatch'):
            response = self.client.get(url)
            self.assertNotIn('class="neoscorpion-call-dispatch"', response.data.decode())
        self.assertNotIn("call_dispatch_alert", service.fueler_context(self.gateway, self.fueler)["rows"][0])

    def test_ack_batch_query_and_schema_are_bounded_and_idempotent(self):
        NeoScorpionCallDispatchAck.__table__.drop(db.engine)
        sync_local_sqlite_schema(self.fixture.app); sync_local_sqlite_schema(self.fixture.app)
        self.assertIn('neoscorpion_call_dispatch_acks', inspect(db.engine).get_table_names())
        for index in range(20): self.fixture._mission(f"UPS{index}", f"N4{index}UP", 20000, index)
        db.session.commit(); statements=[]
        def capture(_c,_cu,sql,*_args): statements.append(sql)
        event.listen(db.engine,"before_cursor_execute",capture)
        try: service.fuel_dispatch_context(self.gateway)
        finally: event.remove(db.engine,"before_cursor_execute",capture)
        self.assertEqual(sum("FROM neoscorpion_call_dispatch_acks " in sql for sql in statements),1)


class TrustedFobInheritanceTest(unittest.TestCase):
    def test_explicit_trusted_lineage_uses_apu_adjusted_fuel_and_preserves_accounting(self):
        fixture = interruption_fixture.NeoScorpionFuelInterruptionTest()
        fixture.setUp(); self.addCleanup(fixture.tearDown)
        operation, mission, assignment = fixture._assignment()
        mission.planned_datetime_utc = datetime.utcnow()+timedelta(hours=2)
        source = fixture._completed_tail_event(operation,"N412UP",(18000,16000,18000),"UPS999")
        work = NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id,tail_number="N412UP")
        db.session.add(work); db.session.flush()
        for code, lbs in zip(("left","ctr","right"),(18000,16000,18000)):
            db.session.add(NeoScorpionFuelTankState(fuel_work_state_id=work.id,tank_code=code,remaining_lbs=lbs))
        service._new_fuel_audit(operation,assignment,fixture.dispatcher,"confirm_tail",
            service.TAIL_SWAP_INHERITED_EVENT_AUDIT_FIELD,"N412UP",str(source.id),"Existing trusted inheritance",
            fuel_work_state=work,now_utc=datetime.utcnow())
        db.session.commit()
        def row():
            db.session.commit()
            return next(r for r in service.fuel_dispatch_context(fixture.gateway)["rows"] if r["mission"].id==mission.id)
        current = row()
        self.assertTrue(current["fuel_on_board_ready"])
        self.assertEqual(current["dispatch_status_key"],"fob-ready")
        self.assertLess(current["fob_projected_neo_lbs"],52000)
        mission.planned_fuel_load=52000
        self.assertFalse(row()["fuel_on_board_ready"])
        mission.planned_fuel_load=50000
        work.tank_states[0].remaining_lbs -= 1
        self.assertIsNone(row()["tail_swap_inherited_event_id"])
        work.tank_states[0].remaining_lbs += 1
        db.session.commit()
        before = NeoScorpionFuelingEvent.query.count()
        result = service.complete_fuel_on_board(fixture.gateway,fixture.dispatcher,assignment.id)
        db.session.commit()
        self.assertTrue(result.changed)
        self.assertEqual(row()["dispatch_status_key"],"fob")
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),before)
        self.assertIsNone(work.off_at_utc)
