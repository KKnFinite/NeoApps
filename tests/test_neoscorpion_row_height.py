"""Gateway-wide Dispatch row sizing, not mission or personal-preference data."""
import unittest
from unittest.mock import patch
from sqlalchemy import text
from app.extensions import db
from app.models import Gateway, NeoScorpionSettings, NeoScorpionFuelingEvent
from app.services import neoscorpion as fuel
from app.services.schema_sync import sync_local_sqlite_schema
from tests import test_neoscorpion_status_audit as fixtures


class RowHeightTest(unittest.TestCase):
    setUp=fixtures.StatusIntegrationTest.setUp
    login=fixtures.StatusIntegrationTest.login

    def save(self, value=None):
        from app.services.neoscorpion_admin_forms import settings_baseline
        data=dict(action='save_settings',red_assignment_alert_threshold_minutes='30',
                  settings_baseline=settings_baseline(self.gateway,'save_settings'),
                  fuel_density_lbs_per_gallon='6.7',planning_inbound_fuel_fallback='12')
        if value is not None:data['mission_row_height_percent']=value
        return self.client.post('/neoscorpion/settings',data=data)

    def test_shared_persistence_default_restore_and_independent_settings(self):
        self.assertEqual(self.settings.mission_row_height_percent,100)
        self.settings.ready_assignment_pulse_enabled=False;db.session.commit()
        admin=self.fixture._login_user('height-admin','master')
        assignment_before={c.name:getattr(self.assignment,c.name) for c in self.assignment.__table__.columns}
        mission_before={c.name:getattr(self.mission,c.name) for c in self.mission.__table__.columns}
        for value in ('75','85','115','130','150','100'):
            before=fuel.neoscorpion_live_revision(self.gateway)['revision']
            self.assertEqual(self.save(value).status_code,302)
            self.assertEqual(self.settings.mission_row_height_percent,int(value))
            self.assertGreater(fuel.neoscorpion_live_revision(self.gateway)['revision'],before)
            self.assertFalse(self.settings.ready_assignment_pulse_enabled)
            self.assertEqual(self.settings.red_assignment_alert_threshold_minutes,30)
        other=self.fixture._login_user('height-second','master')
        for path in ('/neoscorpion/fuel-dispatch','/neoscorpion/fuel-dispatch/live-panel?completion_only=1'):
            response=self.client.get(path)
            html=response.get_json()['html'] if response.is_json else response.get_data(as_text=True)
            self.assertIn('data-mission-row-height-percent="100"',html)
            self.assertNotIn('style="--dispatch-row-height-scale:',html)
        page=self.client.get('/neoscorpion/settings').get_data(as_text=True)
        self.assertIn('Restore Default',page);self.assertIn('98px desktop / 73px mobile',page)
        self.assertEqual(self.settings.updated_by_user_id,admin.id);self.assertNotEqual(admin.id,other.id)
        with patch('app.services.neoscorpion.current_sort_operation',return_value=None):
            self.assertEqual(fuel.settings_context(self.gateway)['settings'].mission_row_height_percent,100)
        g=Gateway(code='XYZ',name='Other gateway');db.session.add(g);db.session.flush()
        db.session.add(NeoScorpionSettings(gateway_id=g.id));db.session.commit()
        self.assertEqual(NeoScorpionSettings.query.filter_by(gateway_id=g.id).one().mission_row_height_percent,100)
        self.assertEqual({c.name:getattr(self.assignment,c.name) for c in self.assignment.__table__.columns},assignment_before)
        self.assertEqual({c.name:getattr(self.mission,c.name) for c in self.mission.__table__.columns},mission_before)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_validation_permissions_omission_and_read_only_polling(self):
        self.login(self.fueler);self.assertEqual(self.save('130').status_code,403)
        self.fixture._login_user('height-admin','master')
        self.assertEqual(self.save('130').status_code,302)
        before=fuel.neoscorpion_live_revision(self.gateway)['revision']
        for value in ('','0','74','151','-1','100.5','oops','100000000000000'):
            self.assertEqual(self.save(value).status_code,400)
            self.assertEqual(self.settings.mission_row_height_percent,130)
        self.assertEqual(self.save().status_code,302)
        self.assertEqual(self.settings.mission_row_height_percent,130)
        for _ in range(2):self.client.get('/neoscorpion/fuel-dispatch/live-panel?completion_only=1')
        self.assertEqual(fuel.neoscorpion_live_revision(self.gateway)['revision'],before)

    def test_additive_sqlite_default_repeat_preserves_custom_height_and_pulse(self):
        self.settings.ready_assignment_pulse_enabled=False;db.session.commit()
        db.session.execute(text('ALTER TABLE neoscorpion_settings DROP COLUMN mission_row_height_percent'));db.session.commit()
        for _ in range(2):sync_local_sqlite_schema(self.fixture.app)
        db.session.commit();db.session.expire_all()
        self.assertEqual(self.settings.mission_row_height_percent,100)
        self.assertFalse(self.settings.ready_assignment_pulse_enabled)
        self.settings.mission_row_height_percent=115;db.session.commit()
        sync_local_sqlite_schema(self.fixture.app);db.session.commit();db.session.expire_all()
        self.assertEqual(self.settings.mission_row_height_percent,115)
