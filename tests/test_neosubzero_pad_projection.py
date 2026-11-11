"""Actual treatment projects to one pad independently of MotherBrain Block-Out."""
import copy
import unittest
from datetime import datetime

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

    def test_recorded_spray_start_uses_event_and_physical_tail_identity(self):
        r, f = self.flow, self.f
        event = NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=f.alpha.id).one()
        earlier = f._departure("UPS-EARLIER", f.alpha.assigned_tail_number, "SDF", 1)
        earlier.actual_block_out_datetime_utc = f.alpha.actual_block_out_datetime_utc
        db.session.commit()
        waiting_version = r.ramp()["queue_version"]
        record = NeoSubZeroSprayRecord(
            sort_date_operation_id=f.operation.id, sort_date_mission_id=f.alpha.id,
            departure_deice_event_id=event.id, tail_number_snapshot=f.alpha.assigned_tail_number,
            pass_number=1, position_number=1, truck_number_snapshot="RADIO-10",
            pass_type="type_i", fluid_name_snapshot="Type I", concentration_percent_snapshot=50,
            started_at_utc=datetime(2026, 9, 1, 2, 10),
        )
        db.session.add(record)
        db.session.commit()

        ramp = r.ramp()
        self.assertEqual(ramp["pad"]["mission_id"], f.alpha.id)
        self.assertEqual(ramp["pad"]["visual_state"], "deicing")
        self.assertEqual([row["mission_id"] for row in ramp["waiting_queue"]], [r.second.id])
        self.assertNotEqual(ramp["queue_version"], waiting_version)
        self.assertIsNone(event.pass1_started_at_utc)

        event.status = "cleared"  # Legacy CLEAR without verified radio still holds actual spray.
        db.session.commit()
        self.assertEqual(r.ramp()["pad"]["mission_id"], f.alpha.id)
        event.radio_state = "complete"
        event.radio_completed_at_utc = datetime(2026, 9, 1, 2, 20)
        event.radio_completed_by_user_id = r.actor.id
        db.session.commit()
        self.assertIsNone(r.ramp()["pad"])
        self.assertEqual(r.ramp()["throat"]["mission_id"], r.second.id)
        self.assertEqual(record.started_at_utc, datetime(2026, 9, 1, 2, 10))

    def test_foreign_spray_evidence_is_flagged_instead_of_occupying_pad_or_queue(self):
        r, f = self.flow, self.f
        event = NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=f.alpha.id).one()
        db.session.add(NeoSubZeroSprayRecord(
            sort_date_operation_id=f.operation.id, sort_date_mission_id=r.second.id,
            departure_deice_event_id=event.id, tail_number_snapshot=r.second.assigned_tail_number,
            pass_number=1, position_number=1, truck_number_snapshot="RADIO-10",
            pass_type="type_i", fluid_name_snapshot="Type I", concentration_percent_snapshot=50,
            started_at_utc=datetime(2026, 9, 1, 2, 10),
        ))
        db.session.commit()
        ramp = r.ramp()
        self.assertIsNone(ramp["pad"])
        self.assertEqual([row["mission_id"] for row in ramp["active_conflicts"]], [f.alpha.id])
        self.assertEqual([row["mission_id"] for row in ramp["waiting_queue"]], [r.second.id])
        with self.assertRaisesRegex(ValueError, "pad"):
            r.mutate(r.second, "save_treatment", treatment_plan="one_type_i",
                     pass1_surface_area="wings_only", pass1_start="22:11")
        db.session.rollback()

    def test_spraying_tail_keeps_its_recorded_pad_after_mission_tail_changes(self):
        r, f = self.flow, self.f
        event = r.mutate(f.alpha, "save_treatment", treatment_plan="one_type_i",
                         pass1_surface_area="wings_only", pass1_start="22:10")
        original_tail = event.tail_number
        parking = SortDateParkingAssignment.query.filter_by(tail_number=original_tail).one()
        parking.tail_number = f.alpha.assigned_tail_number = "N-REPLACEMENT"
        db.session.commit()
        ramp = r.ramp()
        self.assertEqual(ramp["pad"]["tail"], original_tail)
        self.assertEqual(ramp["pad"]["visual_state"], "deicing")
        self.assertIn("identity changed", ramp["pad"]["identity_note"])
        self.assertNotIn(original_tail, [row["tail"] for row in ramp["waiting_queue"]])
        self.assertEqual(event.tail_number, original_tail)

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
