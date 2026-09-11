"""Fresh-request poll budgets and lifecycle eager-load equivalence (local DB)."""
import os
import unittest
from contextlib import nullcontext
from datetime import date, datetime, time
from unittest.mock import patch

from sqlalchemy import event

from app.extensions import db
from app.models import SortDateMission, SortTimelineSettings, User
from app.services import operation_lifecycle
from app.services.neosektor_live_counts import NeoSektorOperationalStateBundle as Bundle
from app.services.neosektor_sheets_compat import clear_neosektor_google_cache
from tests import test_neosektor_routes as fixtures
from tests.test_neosektor_integration_modes import _FakeWorksheet, FAKE_SHEETS_ENV


def legacy_sort_settings(gateway):
    parent = SortTimelineSettings.query.filter_by(gateway_id=gateway.id).first()
    return {str(row.sort_name or '').strip().lower(): row for row in parent.sort_settings} if parent else {}


class PollQueriesTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.NeoSektorRoutesTest()
        self.fixture.setUp()
        self.fixture._login_approved_user('simulator')
        self.day = date(2026, 9, 10)
        self.operation = self.fixture._add_sort_operation(self.day, 'night')
        self.operation.generated_by_user_id = User.query.first().id
        self.fixture._set_sort_window('night', time(22), time(2))
        self.fixture.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE'] = datetime(2026, 9, 11, 0, 30)

    def tearDown(self):
        clear_neosektor_google_cache()
        self.fixture.tearDown()

    def measure(self, path, *, legacy=False, unchanged=False):
        sql, commits = [], []
        def capture(conn, cursor, statement, params, context, many):
            sql.append(' '.join(statement.lower().split()))
        def committed(conn):
            commits.append(True)
        with self.fixture.app.app_context():
            engine = db.engine
            event.listen(engine, 'before_cursor_execute', capture)
            event.listen(engine, 'commit', committed)
            try:
                with (patch.object(operation_lifecycle, '_sort_settings_for_gateway',
                                   side_effect=legacy_sort_settings) if legacy else nullcontext()), (
                        patch.object(Bundle, 'load', side_effect=AssertionError('Unchanged poll built state'))
                        if unchanged else nullcontext()):
                    response = self.fixture.client.get(path)
            finally:
                event.remove(engine, 'before_cursor_execute', capture)
                event.remove(engine, 'commit', committed)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertFalse(commits)
        self.assertFalse(any(s.startswith(('insert ', 'update ', 'delete ')) for s in sql))
        return response.json, sql

    def check_pair(self, path, baseline, *, unchanged=False):
        before, old_sql = self.measure(path, legacy=True, unchanged=unchanged)
        after, sql = self.measure(path, unchanged=unchanged)
        self.assertEqual(after, before)  # Includes revision, refresh status, modes and routing.
        self.assertEqual(sum(s.startswith(('select ', 'with ')) for s in old_sql), baseline)
        self.assertEqual(sum(s.startswith(('select ', 'with ')) for s in sql), baseline - 1)
        self.assertEqual(sum('from sort_timeline_settings' in s for s in old_sql), 1)
        self.assertEqual(sum('from sort_timeline_sort_settings' in s for s in old_sql), 1)
        self.assertEqual(sum('from sort_timeline_settings' in s
                             and 'left outer join sort_timeline_sort_settings' in s for s in sql), 1)
        # Both weekday reads and every other query are unchanged, in order.
        without_timeline = lambda statements: [s for s in statements
            if 'from sort_timeline_settings' not in s and 'from sort_timeline_sort_settings' not in s]
        self.assertEqual(without_timeline(sql), without_timeline(old_sql))
        self.assertEqual(sum('from gateway_sort_matrix' in s for s in sql), 2)
        self.assertEqual(after['changed'], not unchanged)
        if unchanged:
            self.assertNotIn('state', after)
        else:
            self.assertEqual(after['state']['summary']['sort_date'], self.day.isoformat())
        print(f'{path}: SELECT {baseline} -> {baseline-1}; INSERT/UPDATE/DELETE/commit 0')
        return after

    def workflow(self, mode):
        if mode != 'empty':
            db.session.add(SortDateMission(sort_date_operation=self.operation, sort_date=self.day,
                gateway_code=self.fixture.gateway.code, sort_name='night', mission_type='arrival',
                mission_source='manual', wave='1', flight_number='TEST1', origin='OAK', destination='RFD',
                actual_block_in_datetime_utc=datetime(2026, 9, 11, 4)))
            bundle = Bundle.load(self.fixture.gateway, sort_date=self.day, include_routing=True)
            bundle.waves[0].planned_count, bundle.waves[1].planned_count = 22, 11
            bundle.ballmat_wave_counts[0].count = 6
            bundle.ballmats[0].spotter_mode, bundle.ballmats[0].right_first = 2, 2
            bundle.driver_routing_state_payload()
            bundle.operational_settings.integration_mode = mode
        db.session.commit()
        worksheet = _FakeWorksheet()
        clear_neosektor_google_cache()
        with patch.dict(os.environ, FAKE_SHEETS_ENV), patch(
                'app.services.neosektor_sheets_compat._get_worksheet', return_value=worksheet):
            for path in ('/neosektor/live-counts/state', '/neosektor/ballmat/state?side=east',
                         '/neosektor/ballmat/state?side=west'):
                with self.subTest(mode=mode, path=path):
                    baseline = 13 if mode == 'google_primary' else 12
                    initial = self.check_pair(path, baseline)
                    url = path + ('&' if '?' in path else '?') + 'revision=' + initial['revision']
                    self.check_pair(url, 10, unchanged=True)
                    if mode == 'google_primary':
                        worksheet.values['B2'] += 1
                        clear_neosektor_google_cache()
                    elif mode != 'empty':
                        bundle.ballmat_wave_counts[0].count += 1
                        db.session.commit()
                    else:
                        continue
                    changed = self.check_pair(url, baseline)
                    self.assertNotEqual(changed['revision'], initial['revision'])
            self.assertEqual(worksheet.updates, [])
            if mode != 'google_primary':
                self.assertEqual(worksheet.batch_reads, [])

    def test_neo_only_current_sort_counts_and_modes(self):
        self.workflow('neo_only')

    def test_mirror_polls_never_write_google_or_database(self):
        self.workflow('neo_primary_google_mirror')

    def test_google_primary_read_contract(self):
        self.workflow('google_primary')

    def test_missing_sektor_state_remains_read_only(self):
        self.workflow('empty')

    def test_paused_poll_still_skips_lifecycle_revision_and_state_work(self):
        path = '/neosektor/live-counts/state'
        initial, _ = self.measure(path)
        self.fixture.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE'] = datetime(2026, 9, 11, 3)
        with patch.object(operation_lifecycle, '_sort_settings_for_gateway',
                          side_effect=AssertionError('Paused poll loaded lifecycle settings')):
            response, sql = self.measure(path + '?revision=' + initial['revision'], unchanged=True)
        self.assertFalse(response['changed'])
        self.assertFalse(response['refresh']['auto_refresh_enabled'])
        self.assertEqual(response['revision'], initial['revision'])
        self.assertEqual(sum(s.startswith(('select ', 'with ')) for s in sql), 4)

    def test_missing_timeline_and_empty_collection_preserve_defaults(self):
        parent = SortTimelineSettings.query.filter_by(gateway_id=self.fixture.gateway.id).one()
        for row in list(parent.sort_settings):
            db.session.delete(row)
        db.session.commit()
        for missing in (False, True):
            if missing:
                db.session.delete(parent)
                db.session.commit()
            for path in ('/neosektor/live-counts/state', '/neosektor/ballmat/state?side=east'):
                with self.fixture.app.test_request_context(path):
                    self.assertEqual(operation_lifecycle._sort_settings_for_gateway(self.fixture.gateway), {})
                    self.assertEqual(legacy_sort_settings(self.fixture.gateway), {})

    def test_non_target_endpoints_keep_legacy_loading(self):
        for path, method in (('/neosektor/driver-routing/state', 'GET'),
                             ('/neosektor/tunnel-conductor/state', 'GET'),
                             ('/neosektor/live-counts', 'GET'),
                             ('/neoermac/door-view/state', 'GET'),
                             ('/neosektor/ballmat/state', 'POST')):
            with self.fixture.app.test_request_context(path, method=method), patch.object(
                    operation_lifecycle, 'joinedload', side_effect=AssertionError('Out-of-scope eager load')):
                operation_lifecycle._sort_settings_for_gateway(self.fixture.gateway)
