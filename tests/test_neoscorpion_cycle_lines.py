import unittest
from datetime import datetime
from sqlalchemy import inspect

from tests import test_neoscorpion_fuel_interruptions as fixture
from app.extensions import db
from app.models import NeoScorpionFuelCycleHistory, NeoScorpionFuelTankState, SortDateMission
from app.services import neoscorpion as service


class CycleLinesTest(unittest.TestCase):
    setUp = fixture.NeoScorpionFuelInterruptionTest.setUp
    tearDown = fixture.NeoScorpionFuelInterruptionTest.tearDown
    _add_user = fixture.NeoScorpionFuelInterruptionTest._add_user
    _assignment = fixture.NeoScorpionFuelInterruptionTest._assignment
    _completed_tail_event = fixture.NeoScorpionFuelInterruptionTest._completed_tail_event
    _truck = fixture.NeoScorpionFuelInterruptionTest._truck
    _login = fixture.NeoScorpionFuelInterruptionTest._login

    def test_existing_database_adds_the_history_table(self):
        from app.services.schema_sync import _create_missing_application_tables

        NeoScorpionFuelCycleHistory.__table__.drop(db.engine)
        tables = set(inspect(db.engine).get_table_names())
        _create_missing_application_tables(tables)
        self.assertIn('neoscorpion_fuel_cycle_history', inspect(db.engine).get_table_names())

    def test_tail_acknowledgment_does_not_start_a_new_cycle(self):
        operation, mission, assignment = self._assignment()
        mission.assigned_tail_number = "N413UP"
        assignment.transfer_fuel_gallons = 100
        old_fueler = assignment.assigned_fueler_user_id
        old_required = mission.planned_fuel_load
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "tail changed again"):
            service.confirm_assignment_tail(
                self.gateway, self.dispatcher, assignment.id,
                expected_cycle=1, expected_tail="N414UP",
            )
        service.confirm_assignment_tail(
            self.gateway, self.dispatcher, assignment.id,
            expected_cycle=1, expected_tail="N413UP",
        )
        db.session.commit()
        self.assertEqual(assignment.current_cycle_number, 1)
        self.assertEqual(assignment.assigned_fueler_user_id, old_fueler)
        self.assertEqual(assignment.transfer_fuel_gallons, 100)
        self.assertEqual(mission.planned_fuel_load, old_required)
        self.assertEqual(NeoScorpionFuelCycleHistory.query.count(), 0)
        self.assertEqual(SortDateMission.query.count(), 1)
        self.assertFalse(service.fuel_dispatch_context(self.gateway)["rows"][0]["tail_mismatch"])

    def test_new_tail_measurement_is_not_inherited_from_other_mission(self):
        operation, mission, assignment = self._assignment()
        source = self._completed_tail_event(
            operation, "N413UP", (18000, 16000, 18000), "UPS9001"
        )
        mission.assigned_tail_number = "N413UP"
        db.session.commit()
        result = service.confirm_assignment_tail(
            self.gateway, self.dispatcher, assignment.id,
            expected_cycle=1, expected_tail="N413UP",
        )
        db.session.commit()
        self.assertIsNone(result.fuel_work_state)
        row = next(r for r in service.fuel_dispatch_context(self.gateway)["rows"]
                   if r["mission"].id == mission.id)
        self.assertFalse(row["fuel_on_board_ready"])
        self.assertIsNone(row["tail_swap_inherited_event_id"])
        self.assertNotEqual(source.fuel_assignment_id, assignment.id)

    def test_uplift_freezes_completed_line_with_blank_new_resources(self):
        operation, _, _ = self._assignment()
        event = self._completed_tail_event(operation, 'N413UP', (18000, 16000, 18000), 'UPS9002')
        assignment = event.fuel_assignment
        mission = db.session.get(SortDateMission, assignment.sort_date_mission_id)
        for tank in event.tank_snapshots:
            db.session.add(NeoScorpionFuelTankState(fuel_work_state_id=event.fuel_work_state_id,
                tank_code=tank.tank_code, remaining_lbs=tank.remaining_lbs, actual_lbs=tank.actual_lbs))
        db.session.commit()
        count = SortDateMission.query.count()
        result = service.start_follow_up_fuel_cycle(self.gateway, self.dispatcher, assignment.id,
            'uplift', '60', None, None, expected_cycle=1, expected_tail='N413UP')
        db.session.commit()
        self.assertEqual(SortDateMission.query.count(), count)
        self.assertEqual(NeoScorpionFuelCycleHistory.query.one().snapshot['actual_total_display'], '52.0')
        self.assertIsNone(assignment.assigned_fueler_user_id)
        self.assertIsNone(assignment.assigned_truck_id)
        self.assertIsNone(assignment.ready_for_fuel_at_utc)
        self.assertIsNone(result.fuel_work_state.apu_running)
        self.assertEqual(sum(t.remaining_lbs for t in result.fuel_work_state.tank_states), 52000)
        self.assertTrue(all(t.actual_lbs is None for t in result.fuel_work_state.tank_states))
        row = next(r for r in service.fuel_dispatch_context(self.gateway)['rows'] if r['mission'].id == mission.id)
        self.assertFalse(row['load_planning_ready'])
        with self.assertRaisesRegex(ValueError, 'cycle changed'):
            service.start_follow_up_fuel_cycle(self.gateway, self.dispatcher, assignment.id,
                'uplift', '60', None, None, expected_cycle=1)

    def test_fueler_apu_choice_is_available_before_first_save(self):
        _, _, assignment = self._assignment()
        self._login(self.fueler)
        html = self.client.get('/neoscorpion/fueler').get_data(as_text=True)
        self.assertIn('data-apu-use-recommended', html)
        self.assertIn('data-apu-use-manual', html)
        self.assertIn('data-apu-automatic-output', html)
        self.assertIn('name="apu_override_allowance"', html)

    def test_fueler_manual_apu_keeps_recommendation_and_can_switch_back(self):
        _, mission, assignment = self._assignment()
        form = {'assignment_id':str(assignment.id), 'apu_running':'yes', 'apu_source_tank_code':'left',
                'apu_override_present':'1', 'apu_override_enabled':'1', 'apu_override_allowance':'0.4',
                'remaining_left':'10', 'remaining_ctr':'10', 'remaining_right':'10',
                'actual_left':'18', 'actual_ctr':'16', 'actual_right':'18'}
        result = service.save_fueler_entry(self.gateway, self.fueler, form, now_utc=datetime(2026,8,18,3,30))
        db.session.commit()
        state = result.fuel_work_state
        recommendation = state.automatic_apu_allowance_lbs
        self.assertIsNotNone(recommendation)
        self.assertEqual(state.apu_allowance_lbs, 400)
        row = service.fuel_dispatch_context(self.gateway)['rows'][0]
        self.assertEqual(row['neo_fuel_display'], '51.6')
        form['apu_override_enabled'] = '0'
        service.save_fueler_entry(self.gateway, self.fueler, form)
        self.assertEqual(state.automatic_apu_allowance_lbs, recommendation)
        self.assertEqual(state.apu_allowance_lbs, recommendation)

        form['apu_source_tank_code'] = 'invalid'
        with self.assertRaisesRegex(ValueError, 'valid APU source tank'):
            service.save_fueler_entry(self.gateway, self.fueler, form)
        form.update(apu_running='no', apu_source_tank_code='')
        service.save_fueler_entry(self.gateway, self.fueler, form)
        self.assertEqual(state.apu_allowance_lbs, 0)
        self.assertEqual(service.fuel_dispatch_context(self.gateway)['rows'][0]['neo_fuel_display'], '52.0')
