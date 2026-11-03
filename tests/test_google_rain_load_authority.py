"""Current field authority: explicit canonical edits, not legacy source labels."""
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from app.extensions import db
from app.models import NeoRainMissionOverride, NeoRainOverrideAudit, SortDateMission, SortDateOperation
from app.services import google_rain_live_milestones as rain
from app.services import google_motherbrain_live_missions as primary
from app.services.live_collaboration import entity_version
from tests import test_neorain_overrides as fixtures
from tests.test_db_query_batching import sql_work


LOAD_FIELDS = {
    'elmac': ('elmac_completed_at_utc', 'elmac_completed_source'),
    'ramp_load_complete': ('ramp_load_completed_at_utc', 'ramp_load_completed_source'),
    'crew_load_complete': ('crew_load_completed_at_utc', 'crew_load_completed_source'),
}


class RainLoadAuthorityTest(unittest.TestCase):
    setUp = fixtures.RainOverrideTest.setUp
    edit = fixtures.RainOverrideTest.edit

    def import_fields(self, **fields):
        result = rain.apply_google_rain_departure_milestones(self.operation, rows=[dict(
            sheet_row=4, flight_number=self.departure.flight_number,
            destination=self.departure.destination, tail=self.departure.assigned_tail_number,
            **fields)])
        db.session.commit()
        return result

    def test_unknown_and_other_source_labels_do_not_create_load_override_authority(self):
        for field, (timestamp, source) in LOAD_FIELDS.items():
            for label in ('unknown', 'manual', 'neo', 'api', 'alp', 'google_motherbrain'):
                with self.subTest(field=field, source=label):
                    setattr(self.departure, timestamp, datetime(2026, 8, 31, 7))
                    setattr(self.departure, source, label)
                    db.session.commit()
                    result = self.import_fields(**{field: '2:30'})
                    self.assertEqual(result['applied_count'], 1)
                    self.assertEqual(getattr(self.departure, timestamp), datetime(2026, 8, 31, 7, 30))
                    self.assertEqual(getattr(self.departure, source), 'google_rain')
                    self.assertEqual(NeoRainMissionOverride.query.count(), 0)

    def test_actual_authorized_editor_preserves_each_load_against_conflict_and_withdrawal(self):
        for field, (timestamp, source) in LOAD_FIELDS.items():
            with self.subTest(field=field):
                self.assertEqual(self.edit(self.departure, field, '0200').status_code, 200)
                for value in ('2:30', ''):
                    self.import_fields(**{field: value})
                    self.assertEqual(getattr(self.departure, timestamp), datetime(2026, 8, 31, 7))
                    self.assertEqual(getattr(self.departure, source), 'neorain')
                override = NeoRainMissionOverride.query.filter_by(field=field).one()
                self.assertTrue(override.active)
                self.assertIsNone(override.upstream[timestamp])
                self.assertEqual(override.modified_by_user_id, self.actor.id)
        self.assertEqual(NeoRainOverrideAudit.query.filter_by(action='EDIT').count(), 3)

    def test_reset_legacy_unknown_snapshot_resumes_first_google_import(self):
        # No incoming Google fact exists between EDIT and RESET. This is the
        # counterexample to restoring the pre-explicit-override source guard.
        for field, (timestamp, source) in LOAD_FIELDS.items():
            with self.subTest(field=field):
                setattr(self.departure, timestamp, datetime(2026, 8, 31, 7))
                setattr(self.departure, source, 'unknown')
                db.session.commit()
                self.assertEqual(self.edit(self.departure, field, '0210').status_code, 200)
                self.assertEqual(self.edit(self.departure, field, action='reset').status_code, 200)
                self.assertEqual(getattr(self.departure, source), 'unknown')
                self.assertEqual(getattr(self.departure, timestamp), datetime(2026, 8, 31, 7))
                self.import_fields(**{field: '2:30'})
                self.assertEqual(getattr(self.departure, timestamp), datetime(2026, 8, 31, 7, 30))
                self.assertEqual(getattr(self.departure, source), 'google_rain')
                self.assertFalse(NeoRainMissionOverride.query.filter_by(field=field).one().active)

    def test_explicit_clear_retains_null_and_reset_restores_observed_upstream(self):
        for field, (timestamp, source) in LOAD_FIELDS.items():
            with self.subTest(field=field):
                self.assertEqual(self.edit(self.departure, field, '0200').status_code, 200)
                self.assertEqual(self.edit(self.departure, field, action='clear').status_code, 200)
                self.import_fields(**{field: '2:30'})
                self.assertIsNone(getattr(self.departure, timestamp))
                self.assertEqual(getattr(self.departure, source), 'neorain')
                self.assertEqual(self.edit(self.departure, field, action='reset').status_code, 200)
                self.assertEqual(getattr(self.departure, timestamp), datetime(2026, 8, 31, 7, 30))
                self.import_fields(**{field: '2:45'})
                self.assertEqual(getattr(self.departure, timestamp), datetime(2026, 8, 31, 7, 45))

    def test_google_population_revision_withdrawal_and_repeated_noops(self):
        fields = {**LOAD_FIELDS, 'block': ('actual_block_out_datetime_utc', 'actual_block_out_source')}
        for field, (timestamp, source) in fields.items():
            with self.subTest(field=field):
                self.import_fields(**{field: '2:30'})
                self.assertEqual(getattr(self.departure, source), 'google_rain')
                self.import_fields(**{field: '2:45'})
                self.assertEqual(getattr(self.departure, timestamp), datetime(2026, 8, 31, 7, 45))
                self.import_fields(**{field: ''})
                self.assertIsNone(getattr(self.departure, timestamp))
                self.assertEqual(getattr(self.departure, source), 'unknown')
                for value in ('', '2:45'):
                    self.import_fields(**{field: value})
                    version = entity_version(self.departure)
                    self.operation.id; self.departure.id
                    with sql_work() as sql:
                        for _ in range(3):
                            result = self.import_fields(**{field: value})
                            self.assertEqual(result['results'][0]['changed_fields'], [])
                    self.assertEqual(sql['UPDATE'], 0)
                    self.assertEqual(entity_version(self.departure), version)

    def test_imported_flags_cannot_override_an_actual_edit_or_grant_authority(self):
        self.assertEqual(self.edit(self.departure, 'ramp_load_complete', '0200').status_code, 200)
        result = self.import_fields(ramp_load_complete='2:30', override=True,
            authority_handoff=rain.NEO_TO_GOOGLE_AUTHORITY_HANDOFF, reset=True)
        self.assertEqual(result['applied_count'], 1)
        self.assertEqual(self.departure.ramp_load_completed_at_utc, datetime(2026, 8, 31, 7))
        self.assertEqual(NeoRainMissionOverride.query.count(), 1)

    def test_denied_and_stale_edits_do_not_replace_the_protected_fact(self):
        old_version = entity_version(self.departure)
        self.assertEqual(self.edit(self.departure, 'ramp_load_complete', '0200').status_code, 200)
        response = self.client.post('/neorain/override', json=dict(mission_id=self.departure.id,
            field='ramp_load_complete', value='0230', expected_version=old_version))
        self.assertEqual(response.status_code, 409)
        watcher = self.fixture._user('load_watcher', 'watcher', rain_role='watcher')
        db.session.commit(); self.fixture._login(watcher)
        self.assertEqual(self.edit(self.departure, 'ramp_load_complete', '0230').status_code, 403)
        self.import_fields(ramp_load_complete='2:30')
        self.assertEqual(self.departure.ramp_load_completed_at_utc, datetime(2026, 8, 31, 7))

    def test_cross_sort_mission_and_tail_identity_are_preserved(self):
        foreign_op = SortDateOperation(gateway_id=self.gateway.id, gateway_code=self.gateway.code,
            sort_date=self.operation.sort_date + timedelta(days=1), sort_name='night')
        db.session.add(foreign_op); db.session.flush()
        foreign = self.fixture._mission(foreign_op, self.departure.flight_number, 'SDF',
            planned=datetime(2026, 9, 1, 7), tail='N456UP')
        other = self.fixture._mission(self.operation, 'UPS999', 'SDF',
            planned=datetime(2026, 8, 31, 7), tail='N789UP')
        db.session.commit()
        tail = self.departure.assigned_tail_number
        self.import_fields(ramp_load_complete='2:30')
        self.assertIsNone(foreign.ramp_load_completed_at_utc)
        self.assertIsNone(other.ramp_load_completed_at_utc)
        self.assertEqual(self.departure.assigned_tail_number, tail)
        self.assertEqual(self.edit(foreign, 'ramp_load_complete', '0200').status_code, 404)

    def test_explicit_block_in_override_keeps_its_separate_primary_google_contract(self):
        self.assertEqual(self.edit(self.arrival, 'block_in', '0200').status_code, 200)
        result = primary.apply_google_motherbrain_live_mission_batch(self.operation, 'arrival', [dict(
            sheet_row=4, flight_number=self.arrival.flight_number, origin='SDF',
            effective_tail=self.arrival.assigned_tail_number, status='ARR',
            planned_time='8/31 01:00', operational_time='8/31 03:00')])
        db.session.commit()
        self.assertEqual(result['applied_count'], 1, result)
        self.assertEqual(self.arrival.actual_block_in_datetime_utc, datetime(2026, 8, 31, 7))
        self.assertEqual(self.arrival.actual_block_in_source, 'neorain')
        row = NeoRainMissionOverride.query.filter_by(sort_date_mission_id=self.arrival.id, field='block_in').one()
        self.assertEqual(row.upstream['actual_block_in_datetime_utc'], '2026-08-31T08:00:00')
