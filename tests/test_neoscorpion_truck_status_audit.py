"""Physical visit identity, truck travel wording and optional inventory checks."""
import unittest
from datetime import timedelta

from app.extensions import db
from app.models import NeoScorpionFuelingEvent, NeoScorpionTruckInventoryAudit
from app.services import neoscorpion as fuel
from app.services.neoscorpion_segments import WorkIndex
from app.services.neoscorpion_fueling_start import truck_start_gate, fueling_start_state
from app.services.neoscorpion_spear import SpearSettings
from tests import test_neoscorpion_fueler_submission as fixtures


class TruckStatusAuditTest(unittest.TestCase):
    setUp = fixtures.SubmissionTest.setUp
    prepare = fixtures.SubmissionTest.prepare
    save = fixtures.SubmissionTest.save
    off = fixtures.SubmissionTest.off
    final_data = fixtures.SubmissionTest.final_data
    login = fixtures.SubmissionTest.login
    baseline = fixtures.SubmissionTest.baseline
    submit = fixtures.SubmissionTest.submit

    def context(self):
        return fuel.fuel_dispatch_context(self.gateway, include_spear=False)

    def visual(self):
        return next(v for v in self.context()['truck_visuals'] if v['truck_id'] == self.truck.id)

    def test_tail_change_without_current_work_retains_open_physical_visit(self):
        self.prepare()
        old_on = self.work.manual_on_at_utc
        self.mission.assigned_tail_number = 'N492UP'
        self.assignment.active_work_state_id = None  # Legacy tail handoff without a new current visit.
        db.session.commit()
        index = fuel._fuel_work_states_by_assignment_tail([self.assignment])
        self.assertIsInstance(index, WorkIndex)
        self.assertFalse(index)  # No current tail entry; retained visit is still ON.
        self.assertEqual(len(index.all_segments), 1)
        self.assertEqual(self.visual()['activity_label'], 'FUELING · B06')
        self.assertEqual(self.work.manual_on_at_utc, old_on)
        self.assertIsNone(self.work.off_at_utc)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)

    def test_prior_visit_release_same_assignment_preserves_travel_and_current_on(self):
        self.prepare()
        assignments = [self.assignment]
        records, _ = fuel._fuel_start_records(self.operation, assignments,
            fuel._fuel_work_states_by_assignment_tail(assignments), fuel._parking_by_tail(self.operation),
            {self.truck.id: self.selection})
        current = dict(records[0], manual_on=None, confirmed_on=None, on=None,
                       assigned_at=self.start-timedelta(minutes=10))
        release = dict(id=self.assignment.id, work_id=self.work.id+100,
            truck_id=self.truck.id, release=self.start-timedelta(minutes=1), parking='A08')
        gate = truck_start_gate(current, [current], [release], SpearSettings(), at=self.start)
        self.assertEqual(gate['reason'], 'COMING FROM ALPHA · ~1 MIN')
        expired = truck_start_gate(current, [current], [release], SpearSettings(),
                                   at=self.start+timedelta(minutes=2))
        self.assertEqual(expired['reason'], 'TRUCK ON PENDING')
        state = fueling_start_state(records[0], records, [release], SpearSettings(), now=self.start)
        self.assertTrue(state['on_trusted'])
        self.assertEqual((state['reason'], state['arrival']), ('', None))
        self.assertIsNone(self.work.off_at_utc)

    def test_administrative_completion_does_not_release_current_physical_on(self):
        self.prepare()
        self.assignment.completed_at_utc = self.start + timedelta(minutes=5)
        self.assignment.review_status = 'complete'
        self.mission.fuel_status = 'complete'
        db.session.commit()
        self.assertEqual(self.visual()['activity_label'], 'FUELING · B06')
        self.assertIsNone(self.work.off_at_utc)
        from app.services.neoscorpion_spear import _current_resource_owners
        self.assertIn(self.truck.id, _current_resource_owners(self.context()['rows'])[1])

    def test_optional_check_skipped_off_submission_complete_and_live_views(self):
        self.prepare()
        self.save(self.start + timedelta(minutes=10), **self.final_data())
        self.off()
        self.assertFalse(self.selection.inventory_verified)
        self.assertEqual(self.selection.inventory_pending_epoch, self.selection.inventory_epoch)
        self.login(self.dispatcher)
        response = self.client.get('/neoscorpion/fuel-dispatch')
        self.assertEqual(response.status_code, 200)
        self.assertFalse('Truck fuel inventory requires verification' in response.text)
        self.assertFalse('Truck inventory discrepancy' in response.text)
        panel = self.client.get('/neoscorpion/fuel-dispatch/live-panel')
        self.assertEqual(panel.status_code, 200)
        self.assertNotIn('Truck fuel inventory requires verification', panel.json['html'])
        self.submit()
        fuel.complete_fueled_assignment(self.gateway, self.dispatcher, self.assignment.id)
        db.session.commit()
        self.assertEqual(self.selection.current_gallons, 7000)
        self.assertTrue(self.selection.inventory_verified)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 1)
        self.assertFalse(fuel.complete_fueled_assignment(self.gateway, self.dispatcher, self.assignment.id).changed)
        self.assertEqual(NeoScorpionTruckInventoryAudit.query.filter_by(action='discrepancy').count(), 0)

    def test_unknown_inventory_is_distinct_from_optional_off_reconciliation(self):
        self.prepare()
        self.selection.inventory_verified = False
        self.selection.inventory_pending_epoch = None
        db.session.commit()
        self.login(self.dispatcher)
        response = self.client.get('/neoscorpion/fuel-dispatch')
        self.assertIn('Truck inventory discrepancy / unknown baseline', response.text)
        self.assertEqual(self.selection.status, 'available')
        self.assertEqual(self.visual()['activity_label'], 'FUELING · B06')
