from datetime import date, datetime
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import SortDateMission, SortDateOperation, User
from app.services.access_control import (
    backfill_default_gateway_node_roles,
    ensure_default_gateway_and_nodes,
)
from app.services.google_rain_integration_mode import (
    NEO_ONLY,
    NEO_PRIMARY_GOOGLE_MIRROR,
    set_rain_integration_mode,
)
from app.models import NeoRainMissionOverride, NeoRainOverrideAudit, SortDateGoogleMissionLink
from app.services.google_rain_integration_mode import GOOGLE_PRIMARY
from app.services.live_collaboration import entity_version
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoRainOutboundMutationEndpointTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "NeoRainOutboundMutationEndpointTestConfig",
            (),
            {
                "SECRET_KEY": "neorain-outbound-endpoint-test-secret",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "DEFAULT_GATEWAY_TIMEZONE": "America/Chicago",
            },
        )
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        # Explicit test bootstrap mirrors deployment bootstrap; web startup
        # deliberately does not create gateway/node or permission rows.
        self.gateway = ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        db.session.commit()
        self.operation = self._operation(date(2026, 6, 18))
        self.mission = self._mission(self.operation, "UPS0910")
        db.session.commit()
        self.client = self.app.test_client()
        self.simulator = self._user("rain_simulator", "simulator")
        self._login(self.simulator)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_google_primary_explicit_override_saves_without_uncertified_google_write(self):
        with self._current_operation(), patch('app.services.neorain_google_overrides.certified_write') as writer:
            response = self._post('ramp_load_complete', '0237')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['active'])
        self.assertEqual(response.json['sync_status'], 'NEO_ONLY')
        writer.assert_not_called()
        self.assertIsNotNone(self.mission.ramp_load_completed_at_utc)
        self.assertEqual(NeoRainMissionOverride.query.one().field, 'ramp_load_complete')

    def test_edit_permission_and_current_sort_membership_are_required(self):
        watcher = self._user('rain_watcher', 'watcher')
        self._login(watcher)
        with self._current_operation():
            denied = self._post('ramp_load_complete', '0237')
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json['error'], 'Edit access denied.')
        self._login(self.simulator)
        other = self._operation(date(2026, 6, 19))
        mission = self._mission(other, 'UPS0999')
        db.session.commit()
        with self._current_operation():
            wrong = self._post('ramp_load_complete', '0237', mission_id=mission.id)
        self.assertEqual(wrong.status_code, 404)
        self.assertEqual(wrong.json['error'], 'Mission is outside the current sort.')
        with self._current_operation(None):
            absent = self._post('ramp_load_complete', '0237')
        self.assertEqual(absent.status_code, 409)
        self.assertEqual(absent.json['error'], 'No editable current sort.')
        self.assertEqual(NeoRainMissionOverride.query.count(), 0)

    def test_mirror_attempt_occurs_once_after_canonical_neo_commit(self):
        self._set_mode(NEO_PRIMARY_GOOGLE_MIRROR)
        self._link()
        before = entity_version(self.mission)
        def write(mission, field, value):
            # A separate connection observes the completed canonical transaction.
            with db.engine.connect() as connection:
                saved = connection.execute(db.select(SortDateMission.ramp_load_completed_at_utc)
                    .where(SortDateMission.id == mission.id)).scalar_one()
            self.assertEqual(saved, mission.ramp_load_completed_at_utc)
            self.assertEqual(field, 'ramp_load_complete')
            return 'isolated-certified-seam'
        with self._current_operation(), patch('app.services.neorain_google_overrides.certified_write', side_effect=write) as writer:
            response = self._post('ramp_load_complete', '0237', expected_version=before)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['row']['ramp_load_complete'], '02:37')
        self.assertEqual(response.json['sync_status'], 'SYNCED')
        self.assertNotEqual(response.json['version'], before)
        writer.assert_called_once()
        self.assertEqual(NeoRainOverrideAudit.query.filter_by(action='EDIT').count(), 1)

    def test_mirror_failure_preserves_committed_neo_and_never_replays(self):
        self._set_mode(NEO_PRIMARY_GOOGLE_MIRROR)
        self._link()
        from app.services.neorain_google_overrides import attempt_once
        with self._current_operation(), patch('app.services.neorain_google_overrides.certified_write', side_effect=RuntimeError('isolated provider failure')) as writer:
            response = self._post('ramp_load_complete', '0237')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['sync_status'], 'FAILED')
            row = NeoRainMissionOverride.query.one()
            attempt_once(row.id, row.generation)
        writer.assert_called_once()
        db.session.refresh(self.mission)
        self.assertIsNotNone(self.mission.ramp_load_completed_at_utc)
        self.assertEqual(self.mission.ramp_load_completed_source, 'neorain')

    def test_neo_only_commits_without_google_calls(self):
        self._set_mode(NEO_ONLY)
        self._link()
        with self._current_operation(), patch('app.services.neorain_google_overrides.certified_write') as writer:
            response = self._post('crew_load_complete', '0237')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['sync_status'], 'NEO_ONLY')
        writer.assert_not_called()
        self.assertIsNotNone(self.mission.crew_load_completed_at_utc)
        self.assertEqual(self.mission.crew_load_completed_source, 'neorain')

    def test_block_out_row_and_canonical_summary_preserve_derived_variance(self):
        from app.neonodes.neorain.services import neorain_outbound_late_summary
        self._set_mode(NEO_ONLY)
        self.mission.wave = '1'
        self.mission.planned_datetime_utc = datetime(2026, 6, 19, 7, 30)
        db.session.commit()
        with self._current_operation():
            response = self._post('official_block_out', '0237')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['row']['departure_variance'], '+7')
        summary = neorain_outbound_late_summary(self.operation)
        self.assertEqual(summary['first_wave']['late_minutes'], 7)
        self.assertEqual(summary['total']['aircraft_late'], 1)
        with self._current_operation():
            response = self._post_late_inclusion(False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(neorain_outbound_late_summary(self.operation)['total'],
                         dict(aircraft_late=0, late_minutes=0, average='0'))

    def test_late_inclusion_without_verified_google_link_stays_neo_only(self):
        self.mission.wave = '1'
        self.mission.departure_status = 'departed'
        self.mission.departure_status_source = 'neorain'
        db.session.commit()
        for mode, included in ((GOOGLE_PRIMARY, False), (NEO_PRIMARY_GOOGLE_MIRROR, True), (NEO_ONLY, False)):
            with self.subTest(mode=mode):
                self._set_mode(mode)
                with self._current_operation(), patch('app.services.neorain_google_overrides.certified_write') as writer:
                    response = self._post_late_inclusion(included)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json['row']['late_metrics_included'], included)
                self.assertEqual(response.json['row']['departure_status'], 'departed')
                self.assertEqual(response.json['sync_status'], 'NEO_ONLY')
                writer.assert_not_called()

    def test_late_inclusion_stale_and_wrong_sort_missions_are_rejected(self):
        before = entity_version(self.mission)
        self.mission.destination = 'ONT'
        db.session.commit()
        with self._current_operation():
            stale = self._post_late_inclusion(True, expected_version=before)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json['code'], 'stale_version')
        self.assertIsNone(self.mission.late_metrics_included_override)
        other = self._operation(date(2026, 6, 19))
        mission = self._mission(other, 'UPS0999')
        db.session.commit()
        with self._current_operation():
            wrong = self._post_late_inclusion(True, mission_id=mission.id)
        self.assertEqual(wrong.status_code, 404)
        self.assertEqual(wrong.json['error'], 'Mission is outside the current sort.')
        self.assertEqual(NeoRainMissionOverride.query.count(), 0)

    def test_stale_version_rejects_without_neo_or_google_changes(self):
        before = entity_version(self.mission)
        self.mission.destination = 'ONT'
        db.session.commit()
        with self._current_operation(), patch('app.neonodes.neorain.override_routes.edit_override') as mutate, patch('app.services.neorain_google_overrides.certified_write') as writer:
            response = self._post('ramp_load_complete', '0237', expected_version=before)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json['code'], 'stale_version')
        mutate.assert_not_called()
        writer.assert_not_called()
        db.session.refresh(self.mission)
        self.assertEqual(self.mission.destination, 'ONT')
        self.assertNotEqual(entity_version(self.mission), before)
        self.assertIsNone(self.mission.ramp_load_completed_at_utc)

    def test_missing_expected_version_is_rejected(self):
        with self._current_operation():
            response = self.client.post('/neorain/outbound/milestone', json={
                'mission_id': self.mission.id, 'field': 'ramp_load_complete', 'value': '0237'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json['error'], 'Provide the current mission version.')
        self.assertEqual(NeoRainMissionOverride.query.count(), 0)

    def test_invalid_time_and_unsupported_field_are_rejected_without_mutation(self):
        with self._current_operation():
            invalid = self._post('official_block_out', '2560')
            unsupported = self._post('not_a_milestone', '0237')
        self.assertEqual(invalid.status_code, 400)
        self.assertIn('time', invalid.json['error'].lower())
        self.assertEqual(unsupported.status_code, 400)
        self.assertEqual(unsupported.json['error'], 'Choose a valid field.')
        self.assertIsNone(self.mission.actual_block_out_datetime_utc)
        self.assertIsNone(self.mission.elmac_completed_at_utc)
        self.assertEqual(NeoRainMissionOverride.query.count(), 0)

    def test_canonical_commit_failure_never_attempts_google_or_compensation(self):
        self._set_mode(NEO_PRIMARY_GOOGLE_MIRROR)
        self._link()
        with self._current_operation(), patch('app.neonodes.neorain.override_routes.db.session.commit', side_effect=RuntimeError('isolated commit failure')), patch('app.services.neorain_google_overrides.attempt_once') as attempt:
            with self.assertRaisesRegex(RuntimeError, 'isolated commit failure'):
                self._post('ramp_load_complete', '0237')
            db.session.rollback()
        attempt.assert_not_called()
        db.session.refresh(self.mission)
        self.assertIsNone(self.mission.ramp_load_completed_at_utc)
        self.assertEqual(NeoRainMissionOverride.query.count(), 0)
        self.assertEqual(NeoRainOverrideAudit.query.count(), 0)

    def _link(self):
        db.session.add(SortDateGoogleMissionLink(sort_date_operation_id=self.operation.id,
            sort_date_mission_id=self.mission.id, mission_type='departure', source_sheet='Outbound', source_row=3))
        db.session.commit()

    def _post(self, field, value, *, mission_id=None, expected_version=None):
        target_id = mission_id or self.mission.id
        if expected_version is None:
            target = db.session.get(SortDateMission, target_id)
            expected_version = entity_version(target)
        return self.client.post(
            "/neorain/outbound/milestone",
            json={
                "mission_id": target_id,
                "field": field,
                "value": value,
                "expected_version": expected_version,
            },
        )

    def _post_late_inclusion(self, included, *, mission_id=None, expected_version=None):
        target_id = mission_id or self.mission.id
        if expected_version is None:
            expected_version = entity_version(db.session.get(SortDateMission, target_id))
        return self.client.post(
            "/neorain/outbound/late-inclusion",
            json={
                "mission_id": target_id,
                "included": included,
                "expected_version": expected_version,
            },
        )

    def _set_mode(self, mode):
        set_rain_integration_mode(self.gateway, self.operation.sort_name, mode)
        db.session.commit()

    def _current_operation(self, operation=...):
        resolved = self.operation if operation is ... else operation
        return patch(
            "app.neonodes.neorain.routes.current_neorain_outbound_operation",
            return_value=resolved,
        )

    def _operation(self, sort_date):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=sort_date,
            sort_name="night",
        )
        db.session.add(operation)
        db.session.flush()
        return operation

    def _mission(self, operation, flight_number):
        mission = SortDateMission(
            sort_date_operation_id=operation.id,
            sort_date=operation.sort_date,
            gateway_code=operation.gateway_code,
            sort_name=operation.sort_name,
            mission_type="departure",
            mission_source="master",
            flight_number=flight_number,
            origin="RFD",
            destination="LAX",
            timezone="America/Chicago",
            planned_datetime_local=datetime.combine(
                operation.sort_date, datetime.min.time()
            ),
            departure_status="scheduled",
        )
        db.session.add(mission)
        db.session.flush()
        return mission

    def _user(self, username, role):
        user = User(
            username=username,
            email=f"{username}@example.test",
            first_name="Rain",
            last_name="User",
            full_name="Rain User",
            employee_id=f"EMP-{username}",
            email_verified_at=datetime.utcnow(),
            role=role,
            is_active=True,
        )
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role=role)
        db.session.commit()
        return user

    def _login(self, user):
        return self.client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
            follow_redirects=False,
        )


if __name__ == "__main__":
    unittest.main()
