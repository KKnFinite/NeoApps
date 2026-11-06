"""Actual treatment projects to one pad independently of MotherBrain Block-Out."""
import copy
import unittest

from app.extensions import db
from app.models import NeoSubZeroAuditEntry, NeoSubZeroSprayRecord, NeoSubZeroDepartureDeiceEvent, SortDateParkingAssignment
from tests import test_neosubzero_radio as fixtures


class PadProjectionTest(unittest.TestCase):
    def setUp(self):
        self.flow = fixtures.RadioCompletionTest()
        self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)
        self.f = self.flow.f

    def check_cycle(self, blocked_out):
        r, f = self.flow, self.f
        if not blocked_out:
            f.alpha.actual_block_out_datetime_utc = None
            SortDateParkingAssignment.query.filter_by(
                sort_date_operation_id=f.operation.id, tail_number=f.alpha.assigned_tail_number
            ).one().position_code = 'A10'
            db.session.commit()
        before = r.ramp()
        self.assertIsNone(before['pad'])
        second = r.second.id
        event = r.mutate(f.alpha, 'save_treatment', treatment_plan='one_type_i',
                         pass1_surface_area='wings_only', pass1_start='22:10')
        original_start = event.pass1_started_at_utc

        def assert_pad(state):
            counts = NeoSubZeroAuditEntry.query.count(), NeoSubZeroSprayRecord.query.count()
            ramp = r.ramp()
            self.assertIsNotNone(ramp['pad'])
            self.assertEqual((ramp['pad']['mission_id'], ramp['pad']['visual_state']), (f.alpha.id, state))
            self.assertEqual([row['mission_id'] for row in ramp['waiting_queue']], [second])
            self.assertFalse(ramp['active_conflicts'])
            self.assertNotIn(f.alpha.id, [row['mission_id'] for p in ramp['parking_positions'] for row in p['aircraft']])
            self.assertEqual(counts, (NeoSubZeroAuditEntry.query.count(), NeoSubZeroSprayRecord.query.count()))
            self.assertEqual(event.pass1_started_at_utc, original_start)

        assert_pad('deicing')
        r.finish()
        evidence = copy.deepcopy(event.pass_evidence)
        assert_pad('finished')
        r.mutate(f.alpha, 'clear')
        assert_pad('radio-pending')
        with self.assertRaisesRegex(ValueError, 'throat|pad'):
            r.mutate(r.second, 'save_treatment', treatment_plan='one_type_i',
                     pass1_surface_area='wings_only', pass1_start='22:21')
        db.session.rollback()
        r.mutate(f.alpha, 'undo_clear', correction_reason='Mistaken CLEAR')
        assert_pad('finished')
        r.mutate(f.alpha, 'clear')
        cleared = event.cleared_at_utc
        r.mutate(f.alpha, 'radio_complete')
        self.assertIsNone(r.ramp()['pad'])
        self.assertEqual(r.ramp()['throat']['mission_id'], second)
        self.assertNotIn(f.alpha.id, [row['mission_id'] for row in r.ramp()['aircraft']])
        self.assertEqual(event.cleared_at_utc, cleared)
        self.assertEqual(event.pass_evidence, evidence)

    def test_started_queue_aircraft_moves_from_position_10_to_pad(self):
        self.check_cycle(True)

    def test_started_aircraft_at_recorded_position_10_occupies_pad_without_block_out(self):
        self.check_cycle(False)

    def test_parked_start_cannot_bypass_occupied_pad(self):
        r, f = self.flow, self.f
        f.alpha.actual_block_out_datetime_utc = None
        r.second.actual_block_out_datetime_utc = None
        db.session.commit()
        r.mutate(f.alpha, 'save_treatment', treatment_plan='one_type_i',
                 pass1_surface_area='wings_only', pass1_start='22:10')
        before = NeoSubZeroAuditEntry.query.count(), NeoSubZeroSprayRecord.query.count()
        with self.assertRaisesRegex(ValueError, 'throat|pad'):
            r.mutate(r.second, 'save_treatment', treatment_plan='one_type_i',
                     pass1_surface_area='wings_only', pass1_start='22:11')
        db.session.rollback()
        self.assertEqual(before, (NeoSubZeroAuditEntry.query.count(), NeoSubZeroSprayRecord.query.count()))
        self.assertIsNone(NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=r.second.id).one().pass1_started_at_utc)
