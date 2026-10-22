"""Administration guards using actual forms and routes, isolated records only."""
from html.parser import HTMLParser
import json
import re
import unittest
from unittest.mock import patch
from sqlalchemy.exc import IntegrityError
from app.extensions import db
from app.models import NeoScorpionSettings, NeoScorpionFuelTruck, NeoScorpionSortTruck
from tests import test_neoscorpion_dispatch_planning as fixtures


class Forms(HTMLParser):
    def __init__(self, html):
        super().__init__(); self.forms=[]; self.form=None; self.select=None; self.feed(html)
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag=='form': self.form={}; self.forms.append(self.form)
        if self.form is None: return
        if tag=='select': self.select=a.get('name')
        if tag=='option' and self.select and ('selected' in a or self.select not in self.form): self.form[self.select]=a.get('value','')
        if tag=='input' and a.get('name') and a.get('type') not in ('submit','button'):
            if a.get('type') in ('checkbox','radio') and 'checked' not in a: return
            self.form[a['name']]=a.get('value','')
    def handle_endtag(self, tag):
        if tag=='form': self.form=None
        if tag=='select': self.select=None


class AdminReliabilityTest(unittest.TestCase):
    setUp=fixtures.NeoScorpionDispatchPlanningTest.setUp
    tearDown=fixtures.NeoScorpionDispatchPlanningTest.tearDown
    _login_user=fixtures.NeoScorpionDispatchPlanningTest._login_user

    def form(self, action='save_settings', path='/neoscorpion/settings'):
        response=self.client.get(path); self.assertEqual(response.status_code,200)
        return next(f for f in Forms(response.get_data(as_text=True)).forms if f.get('action')==action)

    def test_stale_full_form_cannot_revert_density_when_saving_layout(self):
        self._login_user('admin-a','master')
        a=self.form(); b=dict(a)
        self.assertEqual(self.client.post('/neoscorpion/settings',data={**a,'fuel_density_lbs_per_gallon':'6.8'}).status_code,302)
        response=self.client.post('/neoscorpion/settings',data={**b,'mission_row_height_percent':'115'})
        self.assertEqual(response.status_code,409)
        db.session.expire_all(); self.assertEqual(NeoScorpionSettings.query.one().fuel_density_lbs_per_gallon,6.8)
        self.assertIn('115',response.get_data(as_text=True)); self.assertIn('Open current values',response.get_data(as_text=True))
        body=response.get_data(as_text=True)
        draft=json.loads(re.search(r'data-admin-form-draft>(.*?)</script>',body,re.S)[1])
        self.assertEqual(draft['values']['mission_row_height_percent'],'115')
        self.assertEqual(draft['values']['settings_baseline'],b['settings_baseline'])
        self.assertEqual(next(f for f in Forms(body).forms if f.get('action')=='save_settings')['settings_baseline'],b['settings_baseline'])

    def test_same_setting_stale_and_missing_or_tampered_tokens_fail_closed(self):
        self._login_user('admin-a','master')
        a=self.form()
        self.assertEqual(self.client.post('/neoscorpion/settings',data={**a,'fuel_density_lbs_per_gallon':'6.8'}).status_code,302)
        for token in (a.get('settings_baseline','missing'), '', 'tampered'):
            with self.subTest(token=token):
                response=self.client.post('/neoscorpion/settings',data={**a,'settings_baseline':token,'fuel_density_lbs_per_gallon':'6.9'})
                self.assertEqual(response.status_code,409)
        self.assertEqual(NeoScorpionSettings.query.one().fuel_density_lbs_per_gallon,6.8)

    def test_independent_sections_remain_valid_and_reload_resolves_conflict(self):
        self._login_user('admin-a','master')
        initial=self.form(); planning=self.form('save_assignment_planning_settings'); apu=self.form('save_apu_rates')
        self.client.post('/neoscorpion/settings',data={**initial,'fuel_density_lbs_per_gallon':'6.8'})
        self.assertEqual(self.client.post('/neoscorpion/settings',data={**planning,'assignment_setup_minutes':'8'}).status_code,302)
        self.assertEqual(self.client.post('/neoscorpion/settings',data=apu).status_code,302)
        current=self.form(); self.assertEqual(current['fuel_density_lbs_per_gallon'],'6.8')
        self.assertEqual(self.client.post('/neoscorpion/settings',data={**current,'mission_row_height_percent':'115'}).status_code,302)
        self.assertEqual(NeoScorpionSettings.query.one().fuel_density_lbs_per_gallon,6.8)
        self.assertEqual(NeoScorpionSettings.query.one().mission_row_height_percent,115)

    def truck_form(self, number=' truck-1 ', **extra):
        return dict(action='save_truck',truck_number=number,capacity_gallons='10000',description='Retain this draft',is_active='1',**extra)

    def test_duplicate_create_is_400_retains_entries_and_roster(self):
        self._login_user('admin-a','master')
        self.assertEqual(self.client.post('/neoscorpion/truck-manager',data=self.truck_form()).status_code,302)
        response=self.client.post('/neoscorpion/truck-manager',data=self.truck_form('TRUCK-1'))
        self.assertEqual(response.status_code,400)
        self.assertIn('already exists',response.get_data(as_text=True))
        self.assertIn('Retain this draft',response.get_data(as_text=True))
        self.assertEqual(NeoScorpionFuelTruck.query.count(),1); self.assertEqual(NeoScorpionSortTruck.query.count(),0)
        self.assertEqual(self.client.get('/neoscorpion/truck-manager').status_code,200)

    def test_rename_duplicate_rolls_back_then_valid_edit_and_create_work(self):
        self._login_user('admin-a','master')
        for number in ('ONE','TWO'): self.client.post('/neoscorpion/truck-manager',data=self.truck_form(number))
        one=NeoScorpionFuelTruck.query.filter_by(truck_number='ONE').one()
        response=self.client.post('/neoscorpion/truck-manager',data=self.truck_form('two',truck_id=one.id))
        self.assertEqual(response.status_code,400); self.assertEqual(one.truck_number,'ONE')
        self.assertEqual(self.client.post('/neoscorpion/truck-manager',data=self.truck_form('THREE',truck_id=one.id)).status_code,302)
        self.assertEqual(NeoScorpionFuelTruck.query.count(),2)

    def test_apu_planning_and_spear_have_independent_stale_guards(self):
        self._login_user('admin-a','master')
        for action,field,first,second in (
            ('save_apu_rates','apu_rate_b757','0.31','0.32'),
            ('save_assignment_planning_settings','assignment_setup_minutes','8','9'),
            ('spear','minimum_truck_reserve_gallons','600','700')):
            with self.subTest(section=action):
                path='/neoscorpion/settings/spear' if action=='spear' else '/neoscorpion/settings'
                original=(next(f for f in Forms(self.client.get(path).get_data(as_text=True)).forms if field in f)
                    if action=='spear' else self.form(action))
                self.assertEqual(self.client.post(path,data={**original,field:first}).status_code,302)
                rejected=self.client.post(path,data={**original,field:second})
                self.assertEqual(rejected.status_code,409)
                self.assertIn('Nothing was saved',rejected.get_data(as_text=True))

    def test_database_integrity_failure_rolls_back_and_never_flashes_success_or_sql(self):
        self._login_user('admin-a','master')
        def fail(*args):
            db.session.add(NeoScorpionFuelTruck(gateway_id=self.gateway.id,truck_number='PARTIAL'))
            raise IntegrityError('NEVER EXPOSE RAW SQL',{},Exception('a different constraint'))
        with patch('app.neonodes.neoscorpion.routes.save_truck',side_effect=fail):
            response=self.client.post('/neoscorpion/truck-manager',data=self.truck_form('NEW'))
        self.assertEqual(response.status_code,400)
        self.assertNotIn('FUEL TRUCK SAVED.',response.get_data(as_text=True))
        self.assertNotIn('NEVER EXPOSE RAW SQL',response.get_data(as_text=True))
        self.assertEqual(NeoScorpionFuelTruck.query.count(),0)

    def test_permissions_precede_settings_and_truck_mutations(self):
        self._login_user('read-only-operator','operator')
        for path,data in (('/neoscorpion/settings',{'action':'save_settings'}),
            ('/neoscorpion/settings/spear',{}),('/neoscorpion/truck-manager',self.truck_form())):
            self.assertEqual(self.client.post(path,data=data).status_code,403)
        self.assertEqual(NeoScorpionSettings.query.count(),0)
        self.assertEqual(NeoScorpionFuelTruck.query.count(),0)

    def test_wrong_section_or_gateway_baseline_cannot_be_replayed(self):
        from app.models import Gateway
        from app.services.neoscorpion_admin_forms import settings_baseline
        self._login_user('admin-a','master'); form=self.form()
        other=Gateway(code='XYZ',name='Other');db.session.add(other);db.session.commit()
        for token in (settings_baseline(self.gateway,'save_apu_rates'),settings_baseline(other,'save_settings')):
            response=self.client.post('/neoscorpion/settings',data={**form,'settings_baseline':token,'fuel_density_lbs_per_gallon':'6.9'})
            self.assertEqual(response.status_code,409)
        self.assertEqual(NeoScorpionSettings.query.count(),0)
