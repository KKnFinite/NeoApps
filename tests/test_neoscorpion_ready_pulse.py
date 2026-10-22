"""Shared presentation setting; never an assignment or fueling prerequisite."""
import unittest
from unittest.mock import patch
from sqlalchemy import inspect, text
from app.extensions import db
from app.models import Gateway, NeoScorpionSettings, NeoScorpionFuelingEvent
from app.services import neoscorpion as fuel
from app.services.neoscorpion_schema import NEOSCORPION_ADDITIVE_COLUMNS
from app.services.schema_sync import LOCAL_SQLITE_OPTIONAL_COLUMNS, POSTGRES_OPTIONAL_COLUMNS, sync_local_sqlite_schema
from tests import test_neoscorpion_status_audit as fixtures


class ReadyPulseTest(unittest.TestCase):
    setUp=fixtures.StatusIntegrationTest.setUp
    login=fixtures.StatusIntegrationTest.login

    def test_only_canonical_ready_qualifies_without_arrival(self):
        from types import SimpleNamespace
        from datetime import datetime
        base=fixtures.StatusRulesTest().row()
        self.assertTrue(fuel._ready_to_assign(base))
        for stage,extra in (
            ('pending',dict(required_fuel_lbs=None)),
            ('assigned',dict(assignment=SimpleNamespace(assigned_truck_id=1,assigned_fueler_user_id=2))),
            ('fueling',dict(fuel_work_state=SimpleNamespace(off_at_utc=None,on_at_utc=datetime.utcnow()),fueling_on_trusted=True)),
            ('off',dict(fuel_work_state=SimpleNamespace(off_at_utc=datetime.utcnow()))),
            ('complete',dict(administratively_complete=True)),('review',dict(effective_hold=True)),
            ('fob-ready',dict(fuel_on_board_ready=True)),('fob',dict(fuel_on_board_complete=True))):
            row=dict(base,**extra)
            self.assertEqual(fuel.fueling_status(row)['dispatch_status_key'],stage)
            self.assertFalse(fuel._ready_to_assign(row))

    def save(self, value=None):
        from app.services.neoscorpion_admin_forms import settings_baseline
        data=dict(action='save_settings',red_assignment_alert_threshold_minutes='30',
                  settings_baseline=settings_baseline(self.gateway,'save_settings'),
                  fuel_density_lbs_per_gallon='6.7',planning_inbound_fuel_fallback='12')
        if value is not None:data['ready_assignment_pulse_enabled']=value
        return self.client.post('/neoscorpion/settings',data=data)

    def test_default_on_shared_across_users_sessions_sorts_and_gateway_scoped(self):
        self.assertTrue(self.settings.ready_assignment_pulse_enabled)
        admin=self.fixture._login_user('pulse-admin','master')
        before=fuel.neoscorpion_live_revision(self.gateway)['revision']
        self.assertEqual(self.save('0').status_code,302)
        self.assertFalse(NeoScorpionSettings.query.filter_by(gateway_id=self.gateway.id).one().ready_assignment_pulse_enabled)
        self.assertGreater(fuel.neoscorpion_live_revision(self.gateway)['revision'],before)
        self.assertEqual(self.settings.red_assignment_alert_threshold_minutes,30)
        second=self.fixture._login_user('pulse-second','master')
        for url in ('/neoscorpion/fuel-dispatch','/neoscorpion/fuel-dispatch/live-panel'):
            response=self.client.get(url)
            html=response.get_json()['html'] if response.is_json else response.get_data(as_text=True)
            self.assertIn('data-ready-assignment-pulse="0"',html)
        self.assertEqual(self.settings.updated_by_user_id,admin.id)
        # It is a gateway setting, not a user/sort preference.
        other=Gateway(code='XYZ',name='Other gateway')
        db.session.add(other);db.session.flush()
        db.session.add(NeoScorpionSettings(gateway_id=other.id));db.session.commit()
        self.assertTrue(NeoScorpionSettings.query.filter_by(gateway_id=other.id).one().ready_assignment_pulse_enabled)
        with patch('app.services.neoscorpion.current_sort_operation',return_value=None):
            self.assertFalse(fuel.settings_context(self.gateway)['settings'].ready_assignment_pulse_enabled)
        self.assertEqual(self.save('1').status_code,302)
        self.assertTrue(self.settings.ready_assignment_pulse_enabled)
        self.assertNotEqual(admin.id,second.id)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_invalid_rejected_omission_preserves_false_no_poll_writes(self):
        self.fixture._login_user('pulse-admin','master')
        self.assertEqual(self.save('0').status_code,302)
        before=fuel.neoscorpion_live_revision(self.gateway)['revision']
        for value in ('','false','true','2','oops'):
            self.assertEqual(self.save(value).status_code,400)
            self.assertFalse(self.settings.ready_assignment_pulse_enabled)
        self.assertEqual(self.save().status_code,302)
        self.assertFalse(self.settings.ready_assignment_pulse_enabled)
        for _ in range(2):
            self.client.get('/neoscorpion/fuel-dispatch/live-panel?completion_only=1')
            fuel.neoscorpion_live_revision(self.gateway)
        self.assertEqual(fuel.neoscorpion_live_revision(self.gateway)['revision'],before)

    def test_operator_cannot_change_shared_setting(self):
        self.login(self.fueler)
        self.assertEqual(self.save('0').status_code,403)
        self.assertTrue(self.settings.ready_assignment_pulse_enabled)

    def test_sqlite_additive_default_and_repeat_preserve_explicit_off(self):
        original=(self.settings.id,self.settings.fuel_density_lbs_per_gallon)
        db.session.execute(text('ALTER TABLE neoscorpion_settings DROP COLUMN ready_assignment_pulse_enabled'))
        db.session.commit()
        for _ in range(2):sync_local_sqlite_schema(self.fixture.app)
        db.session.commit();db.session.expire_all()
        self.assertTrue(self.settings.ready_assignment_pulse_enabled)
        self.assertEqual((self.settings.id,self.settings.fuel_density_lbs_per_gallon),original)
        self.settings.ready_assignment_pulse_enabled=False;db.session.commit()
        sync_local_sqlite_schema(self.fixture.app);db.session.commit();db.session.expire_all()
        self.assertFalse(self.settings.ready_assignment_pulse_enabled)
        column=next(c for c in inspect(db.engine).get_columns('neoscorpion_settings') if c['name']=='ready_assignment_pulse_enabled')
        self.assertFalse(column['nullable'])
        for columns in (LOCAL_SQLITE_OPTIONAL_COLUMNS,POSTGRES_OPTIONAL_COLUMNS,NEOSCORPION_ADDITIVE_COLUMNS):
            self.assertEqual(columns['neoscorpion_settings']['ready_assignment_pulse_enabled'],'BOOLEAN NOT NULL DEFAULT TRUE')
