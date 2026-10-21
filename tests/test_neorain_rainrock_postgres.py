"""Rainrock first-insert conflicts and additive migration in disposable schemas."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import TestCase, skipUnless
from unittest.mock import patch
from sqlalchemy import inspect, text
from app.extensions import db
from app.models import NeoRainReportEntry, NeoRainOperationalSetting
from app.services.neorain_ground_time_settings_schema import ensure_neorain_ground_time_settings_table
from tests import test_neorain_rainrock_dashboard as dashboard
from tests import test_neorain_overrides_postgres as pg


@skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class RainrockPostgresTest(TestCase):
    def setUp(self):
        with patch.object(dashboard.daily.fixtures,'RainOverrideTest',pg.RainOverridePostgresTest):
            dashboard.RainrockDashboardTest.setUp(self)
    post=dashboard.RainrockDashboardTest.post
    state=dashboard.RainrockDashboardTest.state
    test_shared_facts=dashboard.RainrockDashboardTest.test_all_ten_inputs_autosave_to_existing_records_and_share_daily_briefing
    test_default_snapshot=dashboard.RainrockDashboardTest.test_default_snapshot_only_new_sorts_and_per_sort_override_is_immutable_from_setting
    test_atomic_stale=dashboard.RainrockDashboardTest.test_invalid_and_stale_saves_are_atomic_zero_and_percent_are_real
    test_history_scope=dashboard.RainrockDashboardTest.test_historical_scope_gateway_and_permissions

    def test_rainrock_and_briefing_simultaneous_first_insert_share_one_identity(self):
        with self.client.session_transaction() as session:cookie=dict(session)
        barrier=Barrier(2);operation_id=self.operation.id
        def save(screen,value):
            client=self.app.test_client()
            with client.session_transaction() as session:session.update(cookie)
            barrier.wait(timeout=5)
            return client.post('/neorain/'+screen+'/inputs?operation_id='+str(operation_id),
                json={'values':{'actual_volume':value},'versions':{'actual_volume':0}}).status_code
        with patch('app.services.neorain_reports.snapshot_current_google',return_value='Fixture'):
            with ThreadPoolExecutor(2) as pool:
                jobs=[pool.submit(save,screen,value) for screen,value in (('rainrock','111'),('daily-briefing','222'))]
                self.assertCountEqual([job.result(timeout=20) for job in jobs],[200,409])
        db.session.expire_all()
        row=NeoRainReportEntry.query.filter_by(field_name='actual_volume',data_source='manual').one()
        self.assertEqual(row.version,1);self.assertIn(row.value,('111','222'))

    def test_migration_repeat_run_preserves_old_setting_and_sort_override(self):
        self.post({'planned_fph':'66000'})
        db.session.add(NeoRainOperationalSetting(gateway_id=self.f.gateway.id,gateway_code='RFD',ground_time_threshold_minutes=135))
        db.session.commit()
        db.session.execute(text('ALTER TABLE neorain_operational_settings DROP COLUMN default_planned_fph'));db.session.commit()
        with patch.dict(self.app.config,{'TESTING':False}):
            for _ in range(2):self.assertTrue(ensure_neorain_ground_time_settings_table(self.app))
        self.assertIn('default_planned_fph',{c['name'] for c in inspect(db.engine).get_columns('neorain_operational_settings')})
        setting=NeoRainOperationalSetting.query.one()
        self.assertEqual(setting.ground_time_threshold_minutes,135);self.assertEqual(setting.default_planned_fph,75000)
        self.assertEqual(NeoRainReportEntry.query.filter_by(field_name='planned_fph').one().value,'66000')
