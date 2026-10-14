"""Preparation and repeated visits are not original-aircraft recovery."""
from datetime import timedelta
from app.extensions import db
from app.models import NeoScorpionFuelWorkState, NeoScorpionFuelingEvent
from app.services import neoscorpion as service
from app.services.neoscorpion_segments import new_work
from tests import test_neoscorpion_operational_timeline as timeline
import unittest


class OutstandingWorkTest(unittest.TestCase):
    setUp = timeline.OperationalTimelineTest.setUp
    prepare = timeline.OperationalTimelineTest.prepare

    def test_busy_truck_preparation_is_not_original_aircraft(self):
        self.prepare()  # truck physically ON a different mission
        self.assignment.created_at = self.start-timedelta(minutes=20)
        db.session.commit()  # assignment existed before its physical ON
        mission = self.fixture._mission('UPS0634', 'N438UP', 25000, 2)
        assignment = self.fixture._assignment(mission)
        assignment.assigned_fueler_user_id = self.fueler.id
        db.session.commit()
        result = service.save_fueler_entry(self.gateway, self.fueler,
            dict(assignment_id=assignment.id, remaining_left='6', remaining_ctr='0', remaining_right='6'),
            now_utc=self.start+timedelta(minutes=1))
        db.session.commit()
        original = result.fuel_work_state
        self.assertIsNone(original.manual_on_at_utc)
        self.assertIsNone(original.confirmed_on_at_utc)
        service.swap_assignment_truck(self.gateway, self.dispatcher, assignment.id, self.truck.id,
            now_utc=self.start+timedelta(minutes=2))
        db.session.commit()
        active_id = assignment.active_work_state_id
        self.assertNotEqual(original.id, active_id)
        # Saving again does not allocate a third visit or confirm physical ON.
        service.save_fueler_entry(self.gateway, self.fueler,
            dict(assignment_id=assignment.id, remaining_left='7', remaining_ctr='0', remaining_right='7'))
        db.session.commit()
        self.assertEqual(assignment.active_work_state_id, active_id)
        self.assertEqual(NeoScorpionFuelWorkState.query.filter_by(fuel_assignment_id=assignment.id).count(), 2)
        row = next(r for r in service.fuel_dispatch_context(self.gateway)['rows'] if r['assignment'].id == assignment.id)
        self.assertFalse(row['fueling_on_trusted'])
        self.assertEqual(row['truck_start_reason'], 'TRUCK STILL FUELING')
        self.assertIn('WAITING FOR TRUCK', row['fuel_status_secondary'])
        self.assertEqual(row['outstanding_segments'], [])
        self.login(self.dispatcher)
        response = self.client.get('/neoscorpion/fuel-dispatch')
        self.assertNotIn(b'ORIGINAL N438UP', response.data)
        with self.assertRaisesRegex(ValueError, 'TRUCK STILL FUELING'):
            service.confirm_fueler_truck_on(self.gateway, self.fueler, assignment.id,
                expected_cycle=1, expected_tail='N438UP', expected_truck_id=self.truck.id)
        db.session.rollback()
        # The existing explicit swap retired preparation before this hotfix;
        # no display read is permitted to alter that audit/history.
        self.assertIsNotNone(original.physical_finalized_at_utc)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)

    def test_retained_same_tail_preparation_remains_reviewable_not_original(self):
        self.prepare()
        self.assignment.created_at = self.start-timedelta(minutes=20)
        mission = self.fixture._mission('UPS0634', 'N438UP', 25000, 2)
        assignment = self.fixture._assignment(mission, truck=self.truck)
        assignment.assigned_fueler_user_id = self.fueler.id
        db.session.commit()
        original = service.save_fueler_entry(self.gateway, self.fueler,
            dict(assignment_id=assignment.id, remaining_left='6', remaining_ctr='0', remaining_right='6')).fuel_work_state
        db.session.commit()
        self.assertIsNone(original.manual_on_at_utc)
        self.assertIsNone(original.confirmed_on_at_utc)
        # Existing retained preparation + another active visit is the exact
        # classification input reported, not a claim that Remaining creates it.
        fresh = new_work(assignment, 'N438UP', self.dispatcher, self.start+timedelta(minutes=3))
        db.session.commit()
        before = (original.id, original.updated_at, original.physical_finalized_at_utc,
                  [(t.tank_code, t.remaining_lbs) for t in original.tank_states])
        self.login(self.dispatcher)
        response = self.client.get('/neoscorpion/fuel-dispatch')
        self.assertFalse(b'ORIGINAL N438UP' in response.data)
        self.assertFalse(b'END ORIGINAL SEGMENT EARLY' in response.data)
        self.assertIn(b'PRIOR PREPARATION N438UP', response.data)
        self.assertIn(f'data-prior-work-id="{original.id}"'.encode(), response.data)
        self.assertEqual(assignment.active_work_state_id, fresh.id)
        self.assertEqual(before, (original.id, original.updated_at, original.physical_finalized_at_utc,
                  [(t.tank_code, t.remaining_lbs) for t in original.tank_states]))

    def test_genuine_earlier_tail_and_same_tail_physical_visits_keep_recovery(self):
        self.prepare()
        original = self.work
        original.off_at_utc = self.start+timedelta(minutes=10)
        fresh = new_work(self.assignment, original.tail_number, self.dispatcher, self.start+timedelta(minutes=11))
        db.session.commit()
        self.login(self.dispatcher)
        response = self.client.get('/neoscorpion/fuel-dispatch')
        self.assertIn(f'PREVIOUS VISIT {original.tail_number}'.encode(), response.data)
        self.assertFalse(b'END ORIGINAL SEGMENT EARLY' in response.data)
        self.assertIn(b'END PREVIOUS VISIT EARLY', response.data)
        self.mission.assigned_tail_number = 'N492UP'
        db.session.commit()
        service.confirm_assignment_tail(self.gateway, self.dispatcher, self.assignment.id,
            expected_cycle=1, expected_tail='N492UP')
        db.session.commit()
        response = self.client.get('/neoscorpion/fuel-dispatch')
        self.assertIn(f'ORIGINAL AIRCRAFT {original.tail_number}'.encode(), response.data)
        self.assertIn(b'END ORIGINAL SEGMENT EARLY', response.data)
        self.assertIn(f'value="{original.id}"'.encode(), response.data)
        self.assertIsNone(original.physical_finalized_at_utc)
        self.assertIsNone(fresh.physical_finalized_at_utc)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)

    login = timeline.OperationalTimelineTest.login
