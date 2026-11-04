"""Final physical outcomes stop reconciling; provider delivery is independent."""
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import event, inspect, text
from app.extensions import db
from app.models import NeoScorpionFuelingEvent as FuelEvent, NeoScorpionFuelWorkState as Work
from app.models import SortTimelineSettings, SortTimelineSortSetting
from app.models import NeoScorpionFuelingEventTankSnapshot as TankSnapshot
from app.models.neoscorpion_spear_observation import NeoScorpionSpearObservation as Observation
from app.services import neoscorpion_spear_decisions as service
from tests import test_neoscorpion_spear_decisions as cases


class Clock(datetime):
    value = datetime(2027, 1, 1)

    @classmethod
    def utcnow(cls):
        return cls.fromisoformat(cls.value.isoformat())


@contextmanager
def sql_counts():
    counts = dict(selects=0, updates=0, observation_updates=0, inserts=0)
    def statement(_connection, _cursor, sql, _params, _context, _many):
        lower = sql.lstrip().lower()
        for verb, name in (('select', 'selects'), ('update', 'updates'), ('insert', 'inserts')):
            if lower.startswith(verb):
                counts[name] += 1
        if lower.startswith('update') and 'neoscorpion_spear_observations' in lower:
            counts['observation_updates'] += 1
    engine = db.engine
    event.listen(engine, 'before_cursor_execute', statement)
    try:
        yield counts
    finally:
        event.remove(engine, 'before_cursor_execute', statement)


class SpearReconciliationTest(unittest.TestCase):
    setUp = cases.SpearDecisionTest.setUp
    observe = cases.SpearDecisionTest.observe
    reference = cases.SpearDecisionTest.reference

    def decision(self, *, sealed=True, known_work=True):
        Clock.value = datetime(2027, 1, 1)
        self.fixture.work.physical_cycle_number = 1
        self.fixture.work.physical_finalized_at_utc = self.now + timedelta(hours=1) if sealed else None
        self.assignment.active_work_state_id = self.fixture.work.id if known_work else None
        self.before = service.scope(self.operation, self.mission, self.assignment, 12)
        identifier = self.observe()
        db.session.commit()
        self.gateway_id = self.gateway.id
        return db.session.get(Observation, identifier)

    def reconcile(self, *, advance=True):
        if advance:
            Clock.value += timedelta(minutes=6)
        with patch.object(service, 'datetime', Clock):
            service.reconcile(self.gateway_id)

    def _sort_window(self):
        parent = SortTimelineSettings.query.filter_by(gateway_id=self.gateway.id).first()
        if parent is None:
            parent = SortTimelineSettings(gateway_id=self.gateway.id, gateway_code=self.gateway.code)
            db.session.add(parent)
            db.session.flush()
        window = SortTimelineSortSetting.query.filter_by(
            gateway_id=self.gateway.id, sort_name='night').first()
        if window is None:
            window = SortTimelineSortSetting(settings_id=parent.id,
                gateway_id=self.gateway.id, gateway_code=self.gateway.code, sort_name='night')
            db.session.add(window)
        window.sort_window_start_local = time(21, 0)
        window.sort_window_end_local = time(5, 0)
        db.session.commit()
        return window

    def test_closed_sort_uses_actual_local_end_including_dst(self):
        from app.services.neoscorpion_spear_expiry import sort_ended
        window = self._sort_window()
        operation = SimpleNamespace(sort_date=date(2026, 11, 3))
        self.assertFalse(sort_ended(operation, window, datetime(2026, 11, 4, 10, 59), 'America/Chicago'))
        self.assertTrue(sort_ended(operation, window, datetime(2026, 11, 4, 11), 'America/Chicago'))
        window.sort_window_end_local = None
        self.assertFalse(sort_ended(operation, window, datetime(2026, 11, 5), 'America/Chicago'))

    def test_unmatched_decision_and_derived_outcome_purge_only_after_close(self):
        from app.services.neoscorpion_spear_expiry import purge_closed_unmatched
        self._sort_window()
        self.assignment.assigned_truck_id = None
        row = self.decision(sealed=False)
        self.reconcile()
        outcome = Observation.query.filter_by(kind='outcome').one()
        self.assertIsNone(row.reconciled_at)
        events_before, works_before = FuelEvent.query.count(), Work.query.count()
        self.assertEqual(purge_closed_unmatched(
            self.gateway.id, now_utc=datetime(2026, 9, 4, 9, 59)), 0)
        self.assertEqual(purge_closed_unmatched(
            self.gateway.id, now_utc=datetime(2026, 9, 4, 10)), 1)
        self.assertIsNone(Observation.query.filter_by(id=row.id).first())
        self.assertIsNone(Observation.query.filter_by(id=outcome.id).first())
        self.assertEqual((FuelEvent.query.count(), Work.query.count()), (events_before, works_before))
        self.assertEqual(purge_closed_unmatched(
            self.gateway.id, now_utc=datetime(2026, 9, 4, 12)), 0)

    def test_connected_physical_decision_is_never_purged(self):
        from app.services.neoscorpion_spear_expiry import purge_closed_unmatched
        self._sort_window()
        row = self.decision(sealed=False)
        self.assertEqual(purge_closed_unmatched(
            self.gateway.id, now_utc=datetime(2026, 9, 4, 12)), 0)
        self.assertIsNotNone(Observation.query.filter_by(id=row.id).first())

    def test_age_bounded_unresolved_retries_keep_late_evidence_eligible(self):
        now = datetime(2027, 1, 1)
        schedule = service._unresolved_retry_after
        self.assertEqual(schedule(now, now), now + timedelta(minutes=5))
        self.assertEqual(schedule(now - timedelta(hours=12), now), now + timedelta(minutes=5))
        self.assertEqual(schedule(now - timedelta(days=1), now), now + timedelta(minutes=30))
        self.assertEqual(schedule(now - timedelta(days=2), now), now + timedelta(minutes=30))
        self.assertEqual(schedule(now - timedelta(days=3), now), now + timedelta(hours=6))
        self.assertEqual(schedule(now - timedelta(days=30), now), now + timedelta(hours=6))

    def test_finalized_observations_have_zero_updates_across_due_intervals(self):
        row = self.decision()
        original_due = row.reconcile_after
        self.reconcile()
        self.assertIsNotNone(row.reconciled_at)
        self.assertEqual(row.reconcile_after, original_due)
        outcome = Observation.query.filter_by(kind='outcome').one()
        self.assertEqual(outcome.payload['payload']['event_ids'], [FuelEvent.query.one().id])
        with sql_counts() as counts:
            for _ in range(10):
                self.reconcile()
        self.assertEqual(counts, dict(selects=10, updates=0, observation_updates=0, inserts=0))

    def test_incomplete_physical_visit_can_gain_final_evidence(self):
        row = self.decision(sealed=False)
        self.reconcile()
        self.assertIsNone(row.reconciled_at)
        provisional = Observation.query.filter_by(kind='outcome').one()
        self.assertIn('physical_visit_not_finalized', provisional.payload['payload']['exclusions'])
        self.fixture.work.physical_finalized_at_utc = self.now + timedelta(hours=1)
        db.session.commit()
        self.reconcile()
        self.assertIsNotNone(row.reconciled_at)
        self.assertEqual(Observation.query.filter_by(kind='outcome').count(), 2)
        self.assertIn('physical_visit_not_finalized', provisional.payload['payload']['exclusions'])

    def test_late_event_after_terminal_assignment_is_not_lost(self):
        old = FuelEvent.query.one()
        fields = {col.name:getattr(old, col.name) for col in FuelEvent.__table__.columns if col.name != 'id'}
        TankSnapshot.query.filter_by(fueling_event_id=old.id).delete(synchronize_session=False)
        db.session.delete(old)
        db.session.commit()
        row = self.decision(sealed=False)
        self.reconcile()
        self.assertIsNone(row.reconciled_at)
        self.assertEqual(Observation.query.filter_by(kind='outcome').one().payload['payload']['exclusions'], ['no_matching_physical_event'])
        db.session.add(FuelEvent(**fields))
        self.fixture.work.physical_finalized_at_utc = self.now + timedelta(hours=1)
        db.session.commit()
        self.reconcile()
        self.assertIsNotNone(row.reconciled_at)
        self.assertEqual(Observation.query.filter_by(kind='outcome').count(), 2)

    def test_unresolved_current_decision_without_event_does_not_finalize(self):
        self.assignment.assigned_truck_id = None
        row = self.decision(sealed=False)
        self.assignment.review_status = 'pending'
        event_row = FuelEvent.query.one()
        db.session.commit()
        self.reconcile()
        self.assertIsNone(row.reconciled_at)
        self.assertEqual(Observation.query.filter_by(kind='outcome').count(), 0)
        self.assertIsNotNone(event_row.id)

    def test_later_decision_boundary_preserves_each_observation_interval(self):
        first = self.decision()
        second_id = self.observe(now=self.now + timedelta(minutes=30))
        db.session.commit()
        self.reconcile()
        outcomes = {row.payload['payload']['decision_id']:row.payload['payload']
                    for row in Observation.query.filter_by(kind='outcome')}
        self.assertEqual(outcomes[first.id]['event_ids'], [FuelEvent.query.one().id])
        self.assertEqual(outcomes[second_id]['event_ids'], [])
        self.assertIn('no_matching_physical_event', outcomes[second_id]['exclusions'])

    def test_later_tail_and_resource_changes_do_not_reassign_original_evidence(self):
        row = self.decision()
        original_tail = row.payload['payload']['tail']
        self.assignment.confirmed_tail_number = 'NEW-PHYSICAL-TAIL'
        self.mission.assigned_tail_number = 'NEW-PHYSICAL-TAIL'
        self.assignment.assigned_fueler_user_id = None
        self.assignment.assigned_truck_id = None
        db.session.commit()
        self.reconcile()
        self.assertIsNotNone(row.reconciled_at)
        self.assertEqual(row.payload['payload']['tail'], original_tail)
        self.assertEqual(Observation.query.filter_by(kind='outcome').one().payload['payload']['event_ids'], [FuelEvent.query.one().id])

    def test_cycle_rollover_preserves_old_identity_and_ignores_new_open_cycle(self):
        row = self.decision(known_work=False)
        self.assignment.current_cycle_number = 2
        self.assignment.review_status = 'pending'
        db.session.add(Work(fuel_assignment_id=self.assignment.id, tail_number='DIFFERENT-TAIL', physical_cycle_number=2))
        db.session.commit()
        self.reconcile()
        self.assertIsNotNone(row.reconciled_at)
        outcome = Observation.query.filter_by(kind='outcome').one()
        self.assertEqual(outcome.cycle_number, 1)
        self.assertEqual(outcome.payload['payload']['decision_id'], row.id)
        self.assertEqual(outcome.payload['payload']['event_ids'], [FuelEvent.query.one().id])

    def test_unknown_work_identity_waits_for_other_open_visits_in_same_cycle(self):
        row = self.decision(known_work=False)
        pending = Work(fuel_assignment_id=self.assignment.id, tail_number=row.payload['payload']['tail'], physical_cycle_number=1)
        db.session.add(pending)
        db.session.commit()
        self.reconcile()
        self.assertIsNone(row.reconciled_at)
        pending.physical_finalized_at_utc = self.now + timedelta(hours=2)
        db.session.commit()
        self.reconcile()
        self.assertIsNotNone(row.reconciled_at)
        self.assertEqual(Observation.query.filter_by(kind='outcome').count(), 1)

    def test_historical_valid_outcome_is_reused_without_rewriting_evidence(self):
        row = self.decision()
        self.reconcile()
        outcome = Observation.query.filter_by(kind='outcome').one()
        original = deepcopy(outcome.payload)
        outcome_id = outcome.id
        # A pre-release historical decision has an outcome but no new marker.
        row.reconciled_at = None
        db.session.commit()
        self.reconcile()
        self.assertIsNotNone(row.reconciled_at)
        self.assertEqual(Observation.query.filter_by(kind='outcome').one().id, outcome_id)
        self.assertEqual(outcome.payload, original)
        self.assertIsNone(outcome.verified_at)

    def test_archive_outage_does_not_reopen_finalized_decisions(self):
        row = self.decision()
        self.reconcile()
        finalized = row.reconciled_at
        with patch('app.services.neoscorpion_learning_vault.export_decision_record', side_effect=RuntimeError('offline')):
            self.assertEqual(service.drain(self.gateway.id), 0)
        self.assertEqual(row.reconciled_at, finalized)
        self.assertIsNone(Observation.query.filter_by(kind='outcome').one().verified_at)
        with sql_counts() as counts:
            self.reconcile()
        self.assertEqual(counts['observation_updates'], 0)

    def test_transaction_failure_rolls_back_outcome_and_finalization(self):
        row = self.decision()
        identifier = row.id
        with patch.object(db.session, 'commit', side_effect=RuntimeError('crash before commit')):
            with self.assertRaises(RuntimeError):
                self.reconcile()
        db.session.rollback()
        self.assertIsNone(db.session.get(Observation, identifier).reconciled_at)
        self.assertEqual(Observation.query.filter_by(kind='outcome').count(), 0)
        self.reconcile()
        self.assertIsNotNone(db.session.get(Observation, identifier).reconciled_at)
        self.assertEqual(Observation.query.filter_by(kind='outcome').count(), 1)

    def test_excluded_final_outcome_stays_excluded_and_tail_owned(self):
        self.assignment.assigned_truck_id = None
        row = self.decision()
        self.reconcile()
        outcome = Observation.query.filter_by(kind='outcome').one()
        self.assertIsNotNone(row.reconciled_at)
        self.assertFalse(outcome.payload['payload']['qualified'])
        self.assertEqual(outcome.payload['payload']['exclusions'], ['no_matching_physical_event'])
        self.assertEqual(row.payload['payload']['tail'], self.fixture.work.tail_number)
        self.assertFalse(outcome.payload['training_eligible'])

    def test_schema_bootstrap_is_additive_idempotent_and_preserves_legacy_payload(self):
        from app.services.schema_sync import sync_database_schema
        row = self.decision()
        self.fixture.work.visit_number = 1
        db.session.commit()
        identifier, original = row.id, deepcopy(row.payload)
        db.session.remove()
        with db.engine.begin() as connection:
            connection.execute(text('DROP INDEX ix_spear_decision_unresolved'))
            connection.execute(text('ALTER TABLE neoscorpion_spear_observations DROP COLUMN reconciled_at'))
        sync_database_schema(self.fixture.app)
        db.session.commit()
        sync_database_schema(self.fixture.app)
        db.session.commit()
        self.assertIn('reconciled_at', {col['name'] for col in inspect(db.engine).get_columns(Observation.__tablename__)})
        self.assertIn('ix_spear_decision_unresolved', {index['name'] for index in inspect(db.engine).get_indexes(Observation.__tablename__)})
        legacy = db.session.get(Observation, identifier)
        self.assertEqual(legacy.payload, original)
        self.assertIsNone(legacy.reconciled_at)

    def test_legacy_outcome_set_order_does_not_duplicate_immutable_evidence(self):
        from app.services.neoscorpion_prof import event_math
        event_row = FuelEvent.query.one()
        event_row.performance_snapshot = {}
        row = self.decision()
        math = event_math(event_row)
        reasons = sorted(set([*math['fueler_reasons'], *math['truck_reasons']]), reverse=True)
        record = dict(decision_id=row.id, event_ids=[event_row.id], qualified=False,
                      exclusions=reasons, comparison_conclusion='observed_only')
        identifier = service._enqueue('outcome', self.assignment, self.gateway_id, record,
                                      service._hash(['outcome', row.id, record]))
        db.session.commit()
        self.reconcile()
        self.assertIsNotNone(row.reconciled_at)
        outcome = Observation.query.filter_by(kind='outcome').one()
        self.assertEqual(outcome.id, identifier)
        self.assertEqual(outcome.payload['payload']['exclusions'], reasons)


if __name__ == '__main__':
    unittest.main()
