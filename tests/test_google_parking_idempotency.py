"""Google synchronization preserves placement identity; manual actions stay explicit."""
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import event

from app.extensions import db
from app.models import MotherBrainAlert, SortDateMission, SortDateOperation, SortDateParkingAssignment, User
from app.services.google_motherbrain_parking import apply_google_motherbrain_parking_batch
from app.services.parking_plan import assign_tail_to_lane, set_tail_operational_status
from app.services.parking_plan_collaboration import parking_plan_revision
from tests import test_google_motherbrain_parking as contracts


@contextmanager
def sql_counts():
    counts = Counter()
    def executed(connection, cursor, statement, parameters, context, many):
        verb = statement.split(None, 1)[0].upper()
        counts[verb] += 1
        if verb in ('UPDATE', 'INSERT'):
            for table in ('sort_date_parking_assignments', 'motherbrain_alerts'):
                if table in statement:
                    counts[(verb, table)] += len(parameters) if many else 1
    engine = db.engine
    event.listen(engine, 'before_cursor_execute', executed)
    try:
        yield counts
    finally:
        event.remove(engine, 'before_cursor_execute', executed)


class GoogleParkingIdempotencyTest(contracts.GoogleMotherBrainParkingTest):
    def snapshot(self):
        return {row.tail_number: (row.ramp_code, row.position_code, row.lane_number,
                row.assigned_at, row.assigned_by_user_id, row.updated_at, row.note, row.is_hot)
                for row in SortDateParkingAssignment.query.order_by(SortDateParkingAssignment.id).all()}

    def test_ten_identical_normalized_polls_preserve_attribution_revision_and_zero_writes(self):
        user = User(username='original-coordinator', password_hash='isolated-no-login')
        other = User(username='google-poll-operator', password_hash='isolated-no-login')
        db.session.add_all([user, other])
        db.session.flush()
        rows = []
        for i, position in enumerate(('A01', 'B01', 'C01', 'D01', 'E01', 'R01')):
            tail = f'N{400+i}UP'
            self._add_tail(tail)  # All share SDF; physical tails remain distinct.
            assignment = self._park(tail, position)
            assignment.assigned_at = datetime(2026, 8, 7, 20)
            assignment.assigned_by_user_id = user.id
            assignment.note = 'Keep coordinator context'
            rows.append(dict(effective_tail=tail.lower(), parking_value=position.lower()))
        db.session.commit()
        before, revision = self.snapshot(), parking_plan_revision(self.operation)
        with sql_counts() as counts, patch('app.services.parking_plan.promote_secondary_parking_slots') as promote:
            for _ in range(10):
                result = apply_google_motherbrain_parking_batch(self.operation, rows, user=other)
                self.assertEqual(result['applied_count'], 6)
                db.session.commit()
            promote.assert_not_called()
        self.assertEqual(counts['UPDATE'], 0)
        self.assertEqual(counts['INSERT'], 0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(parking_plan_revision(self.operation), revision)

    def test_single_move_changes_only_intended_assignment_then_becomes_idle(self):
        for tail, position in (('N401UP', 'A01'), ('N402UP', 'B01')):
            self._add_tail(tail)
            self._park(tail, position)
        db.session.commit()
        untouched = self.snapshot()['N402UP']
        with sql_counts() as counts:
            self._apply('N401UP', 'C2')
            db.session.commit()
        self.assertEqual(counts['UPDATE', 'sort_date_parking_assignments'], 1)
        self.assertEqual(self._assignment('N401UP').position_code, 'C02')
        self.assertEqual(self.snapshot()['N402UP'], untouched)
        with sql_counts() as counts:
            self._apply('N401UP', 'C02')
            db.session.commit()
        self.assertEqual(counts['UPDATE'], 0)

    def test_lone_google_secondary_keeps_native_effective_primary_without_churn(self):
        self._add_tail('N401UP')
        self._apply('N401UP', 'A1-b')
        db.session.commit()
        before = self.snapshot()
        self.assertEqual(self._assignment('N401UP').lane_number, 1)
        with sql_counts() as counts:
            for _ in range(10):
                self._apply('N401UP', ' A01-B ')
                db.session.commit()
        self.assertEqual(counts['UPDATE'], 0)
        self.assertEqual(self.snapshot(), before)

    def test_unchanged_row_still_repairs_orphan_secondary_once(self):
        for tail, position, lane in (('N401UP', 'A01', 1), ('N402UP', 'B01', 2)):
            self._add_tail(tail)
            self._park(tail, position, lane)
        db.session.commit()
        original = self.snapshot()['N401UP']
        self._apply('N401UP', 'A1')
        db.session.commit()
        self.assertEqual(self._assignment('N402UP').lane_number, 1)
        self.assertEqual(self.snapshot()['N401UP'], original)
        with sql_counts() as counts:
            self._apply('N401UP', 'A1')
            db.session.commit()
        self.assertEqual(counts['UPDATE'], 0)

    def test_occupied_secondary_is_not_mistaken_for_effectively_unchanged_primary(self):
        for tail, lane in (('N401UP', 1), ('N402UP', 2)):
            self._add_tail(tail)
            self._park(tail, 'A01', lane)
        db.session.commit()
        self._apply('N401UP', 'A1-b')
        db.session.commit()
        self.assertEqual(self._assignment('N401UP').lane_number, 1)
        self.assertIsNone(self._assignment('N402UP').position_code)
        with sql_counts() as counts:
            self._apply('N401UP', 'A1-b')
            db.session.commit()
        self.assertEqual(counts['UPDATE'], 0)

    def test_manual_same_slot_reassignment_and_optional_attributes_remain_explicit(self):
        self._add_tail('N401UP')
        row = self._park('N401UP', 'A01')
        row.assigned_at = datetime(2020, 1, 1)
        db.session.commit()
        assign_tail_to_lane(self.operation, 'N401UP', 'A', 'A01', 1, note='Operator edit')
        db.session.commit()
        self.assertNotEqual(row.assigned_at, datetime(2020, 1, 1))
        self.assertEqual(row.note, 'Operator edit')
        assign_tail_to_lane(self.operation, 'N401UP', 'A', 'A01', 1,
                            note='Explicit attribute change', preserve_unchanged=True)
        db.session.commit()
        self.assertEqual(row.note, 'Explicit attribute change')

    def test_existing_physical_alerts_are_stable_but_corrections_resolve_them(self):
        self._add_tail('N967UP')
        self._add_tail('N457UP')
        self._park('N457UP', 'E03')
        self._apply('N967UP', 'E04')
        db.session.commit()
        alert = MotherBrainAlert.query.filter_by(active=True).one()
        before = alert.updated_at
        with sql_counts() as counts:
            for _ in range(10):
                self._apply('N967UP', 'E4')
                db.session.commit()
        self.assertEqual(counts['UPDATE'], 0)
        self.assertEqual(alert.updated_at, before)
        self._apply('N457UP', 'E02')
        db.session.commit()
        self.assertFalse(alert.active)

    def test_hot_and_spare_status_survive_unchanged_parking(self):
        for tail, position, status in (('N401UP', 'A01', 'hot'), ('N402UP', 'B01', 'spare')):
            self._add_tail(tail)
            self._park(tail, position)
            if status == 'spare':
                SortDateMission.query.filter_by(assigned_tail_number=tail).one().mission_type = 'arrival'
            set_tail_operational_status(self.operation, tail, status)
        db.session.commit()
        before = self.snapshot()
        with sql_counts() as counts:
            self._apply('N401UP', 'A1')
            self._apply('N402UP', 'B1')
            db.session.commit()
        self.assertEqual(counts['UPDATE'], 0)
        self.assertEqual(self.snapshot(), before)

    def test_rollover_keeps_previous_operation_placement_untouched(self):
        self._add_tail('N401UP')
        old = self._park('N401UP', 'A01')
        db.session.commit()
        old_id = old.id
        self.operation = SortDateOperation(gateway_id=self.gateway.id, gateway_code='RFD',
            sort_name='day', sort_date=self.operation.sort_date)
        db.session.add(self.operation)
        db.session.flush()
        self._add_tail('N401UP')
        self._apply('N401UP', 'B2')
        db.session.commit()
        self.assertEqual(db.session.get(SortDateParkingAssignment, old_id).position_code, 'A01')
        self.assertEqual(self._assignment('N401UP').position_code, 'B02')
