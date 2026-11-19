"""STOP SIGN is Conductor-owned current-Sort presentation, never routing state."""
from datetime import timedelta,datetime,time
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from sqlalchemy import text
from app.extensions import db
from app.models import NeoSektorSortState
from app.services import neosektor_live_counts as live
from tests import test_neosektor_discharge_controls as contracts
from tests.html_contracts import document


class StopSignTest(TestCase):
    setUp=contracts.DischargeControlsTest.setUp
    tearDown=contracts.DischargeControlsTest.tearDown
    post=contracts.DischargeControlsTest.post
    state=contracts.DischargeControlsTest.state
    stored=contracts.DischargeControlsTest.stored
    arrival=contracts.DischargeControlsTest.arrival
    back=contracts.DischargeControlsTest.back
    cut=contracts.DischargeControlsTest.cut
    prepare_second=contracts.DischargeControlsTest.prepare_second

    def as_role(self,role):
        from flask import g
        from app.models import User
        g.pop('_login_user',None);self.client.post('/logout');g.pop('_login_user',None)
        if User.query.filter_by(username=f'sektor_{role}_user').first():
            self.client.post('/login',data={'email':f'sektor_{role}@example.test','password':'TestPassword123!'})
        else:self.fixture._login_approved_user(role=role)

    def command(self,enabled):
        routing=self.state()['state']['routing']
        return dict(action='stop_sign',enabled=enabled,expected_version=routing['stop_sign_version'],expected_scope=routing['stop_sign_scope'])

    def test_default_persistence_cross_user_refresh_and_new_sort_reset(self):
        initial=self.state()
        self.assertFalse(initial['state']['routing']['stop_sign'])
        self.assertEqual(initial['state']['routing']['stop_sign_version'],0)
        command=self.command(True);state=self.post('discharge-controls',command)['state']
        self.assertTrue(state['routing']['stop_sign'])
        db.session.expire_all()
        self.assertTrue(NeoSektorSortState.query.one().stop_sign)
        self.assertEqual(NeoSektorSortState.query.one().stop_sign_version,1)
        self.as_role('master')
        before=self.stored()
        changed=self.state('/neosektor/driver-routing/state?revision='+initial['revision'])
        self.assertTrue(changed['changed']);self.assertTrue(changed['state']['routing']['stop_sign'])
        self.assertTrue(self.state('/neosektor/tunnel-conductor/state')['state']['routing']['stop_sign'])
        self.assertNotEqual(changed['state']['routing_watch']['version'],initial['state']['routing_watch']['version'])
        self.assertEqual(self.stored(),before)
        next_day=self.day+timedelta(days=1)
        self.fixture._add_sort_operation(next_day,'night')
        self.fixture.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE']=datetime.combine(next_day,time(23))
        fresh=self.post('wave',dict(wave='first',value=8))['state']
        self.assertFalse(fresh['routing']['stop_sign']);self.assertEqual(fresh['routing']['stop_sign_version'],0)
        self.assertTrue(NeoSektorSortState.query.filter_by(sort_date=self.day).one().stop_sign)
        self.post('discharge-controls',command,409) # Old Sort never mutates the new Sort.
        self.assertFalse(self.state()['state']['routing']['stop_sign'])

    def test_stale_duplicate_aba_scope_and_invalid_commands_are_atomic(self):
        original=self.command(True)
        self.post('discharge-controls',original)
        before=self.stored()
        self.post('discharge-controls',original,409)
        self.assertEqual(self.stored(),before)
        self.post('discharge-controls',self.command(False))
        self.post('discharge-controls',original,409) # OFF -> ON -> OFF cannot revive an old version.
        for extra,status in [(dict(enabled='true'),400),(dict(expected_version=True),409),
                (dict(expected_version=-1),409),(dict(expected_scope='foreign-gateway:sort'),409),
                (dict(expected_scope=None),409)]:
            before=self.stored()
            self.post('discharge-controls',{**self.command(True),**extra},status)
            self.assertEqual(self.stored(),before)
        self.assertFalse(self.state()['state']['routing']['stop_sign'])

    def test_permissions_and_csrf_server_authority(self):
        command=self.command(True);before=self.stored()
        self.as_role('watcher')
        self.post('discharge-controls',command,403)
        self.assertEqual(self.stored(),before)
        self.as_role('simulator')
        # Enforce the real session-bound CSRF guard on the same endpoint.
        with patch.dict(self.fixture.app.config,CSRF_PROTECT_TESTING=True):
            self.post('discharge-controls',command,400)
        self.assertEqual(self.stored(),before)
        self.assertFalse(self.state()['state']['routing']['stop_sign'])
        self.post('discharge-controls',self.command(True))
        self.as_role('watcher')
        page=document(self.client.get('/neosektor/tunnel-conductor'))
        self.assertIn('disabled',page.one('input',**{'data-discharge-stop':None}).attrs)

    def test_routes_live_counts_conductor_and_middle_bar_continue_under_stop(self):
        self.arrival('1');self.arrival('2')
        before=self.state()['state']
        self.post('discharge-controls',self.command(True))
        for side in ('east','west'):self.back(side,True)
        self.post('settings',dict(first_override='west',second_override='east'))
        active=self.state()['state']
        self.assertTrue(active['routing']['stop_sign'])
        self.assertEqual(active['routing']['routes']['first']['direction'],'west')
        self.assertEqual(active['routing']['routes']['second']['direction'],'east')
        self.assertEqual([c['side'] for c in active['routing']['bay_priority']],['east','west'])
        for path in ('/neosektor/driver-routing','/neosektor/driver-routing?tv=1'):
            dom=document(self.client.get(path));board=dom.one('main',cls='driver-routing')
            sections=[c for c in board.children if hasattr(c,'tag')]
            self.assertEqual(len(sections),3)
            self.assertEqual(sections[0].attrs['data-driver-wave'],'first')
            self.assertIn('driver-bay-priority',sections[1].attrs['class'])
            self.assertEqual(sections[2].attrs['data-driver-wave'],'second')
            for wave in (sections[0],sections[2]):
                self.assertEqual(wave.attrs['data-stop-sign'],'true')
                stop=wave.one(**{'data-driver-stop':None})
                self.assertNotIn('hidden',stop.attrs)
                self.assertEqual([t.text for t in stop.findall('text')],['STOP','Wait for instructions'])
                svg=stop.one('svg');self.assertEqual(svg.attrs['preserveaspectratio'],'xMidYMid meet')
                self.assertFalse(stop.findall('image'))
                wave.one('h2',cls='driver-wave-label')
            self.assertEqual([c.text.strip() for c in sections[1].findall('article') if 'hidden' not in c.attrs],['DISCHARGE EAST','DISCHARGE WEST'])
        self.cut(True,False)
        cut=self.state()['state'];self.assertTrue(cut['routing']['cut_discharge'])
        self.assertEqual(cut['routing']['bay_priority'],[])
        self.assertTrue(cut['routing']['stop_sign'])
        self.cut(False,True)
        self.back('east',False,True)
        self.assertEqual([b['side'] for b in self.state()['state']['routing']['bay_priority']],['west'])
        # Canonical downstream readouts retain routing even when the Driver view is STOP.
        conductor=self.state('/neosektor/tunnel-conductor/state')['state']
        self.assertEqual(conductor['routing']['routes'],self.state()['state']['routing']['routes'])
        self.assertEqual(conductor['ballmat_routing']['first'],'ROUTING WEST')
        off=self.post('discharge-controls',self.command(False))['state']
        self.assertFalse(off['routing']['stop_sign'])
        self.assertEqual(off['routing']['routes'],conductor['routing']['routes'])
        for wave in document(self.client.get('/neosektor/driver-routing')).findall(**{'data-driver-wave':None}):
            self.assertEqual(wave.attrs['data-stop-sign'],'false')
            self.assertIn('hidden',wave.one(**{'data-driver-stop':None}).attrs)

    def test_stop_does_not_suppress_automatic_cut_or_wave_lifecycle(self):
        self.prepare_second()
        self.post('discharge-controls',self.command(True))
        after=self.post('wave',dict(wave='second',value=0))['state']
        self.assertTrue(after['routing']['stop_sign']);self.assertTrue(after['routing']['cut_discharge'])
        self.assertEqual(after['waves'][1]['left'],'ALL UP')
        self.assertTrue(after['routing']['discharge_auto_fired'])

    def test_google_primary_stop_is_sort_local_without_sheet_writes(self):
        import os
        from app.models import NeoSektorOperationalSetting
        from tests.test_neosektor_integration_modes import FAKE_SHEETS_ENV,_FakeWorksheet,_complete_sheet_values
        from app.services.neosektor_sheets_compat import clear_neosektor_google_cache
        NeoSektorOperationalSetting.query.one().integration_mode='google_primary';db.session.commit()
        sheet=_FakeWorksheet(_complete_sheet_values())
        with patch.dict(os.environ,FAKE_SHEETS_ENV),patch('app.services.neosektor_sheets_compat._get_worksheet',return_value=sheet):
            try:
                command=self.command(True);before=list(sheet.updates)
                self.post('discharge-controls',command)
                self.assertTrue(self.state()['state']['routing']['stop_sign'])
                self.assertEqual(sheet.updates,before)
                self.post('discharge-controls',self.command(False))
                self.assertFalse(self.state()['state']['routing']['stop_sign'])
            finally:clear_neosektor_google_cache()

    def test_same_control_row_responsive_overlay_and_additive_schema(self):
        response=self.client.get('/neosektor/tunnel-conductor')
        from tests.presentation_contracts import assert_scripts_parse
        assert_scripts_parse(self,response)
        assert_scripts_parse(self,self.client.get('/neosektor/driver-routing'))
        dom=document(response)
        row=dom.one('div',cls='tunnel-back-pickup-row')
        self.assertEqual([c.text.strip() for c in row.findall('label')],['BACK PICKUP EAST','STOP SIGN','BACK PICKUP WEST'])
        self.assertEqual(len(row.findall('input')),3)
        for path in ('/neosektor/ebm','/neosektor/wbm','/neosektor/live-counts'):
            page=document(self.client.get(path));self.assertFalse(page.findall(**{'data-discharge-stop':None}))
        css=Path('app/static/css/neosektor_driver_routing.css').read_text(encoding='utf-8')
        rules=css[css.index('/* STOP occupies'):]
        # The overlay cannot participate in intrinsic sizing, alter the middle
        # bar, or change outer grid rows at any viewport/aspect ratio.
        self.assertIn('position:absolute; grid-area:2 / 1 / 3 / 2; inset:0',rules)
        self.assertIn('visibility:hidden',rules)
        self.assertIn('width:100%; height:100%',rules)
        self.assertNotIn('.neosektor-driver-board',rules)
        self.assertNotIn('driver-bay-priority',rules)
        self.assertNotIn('@media',rules)
        self.assertNotIn('vh',rules);self.assertNotIn('vw',rules)
        self.assertIn('minmax(0,1fr) auto minmax(0,1fr)',Path('app/static/css/neosektor_discharge_controls.css').read_text())
        from app.services.schema_sync import LOCAL_SQLITE_OPTIONAL_COLUMNS,POSTGRES_OPTIONAL_COLUMNS,sync_database_schema
        for col in ('stop_sign','stop_sign_version'):
            self.assertEqual(LOCAL_SQLITE_OPTIONAL_COLUMNS['neosektor_sort_states'][col],POSTGRES_OPTIONAL_COLUMNS['neosektor_sort_states'][col])
        db.session.remove()
        with db.engine.begin() as conn:
            for col in ('stop_sign','stop_sign_version'):conn.execute(text('ALTER TABLE neosektor_sort_states DROP COLUMN '+col))
        for _ in range(2):sync_database_schema(self.fixture.app);db.session.commit()
        self.assertFalse(NeoSektorSortState.query.one().stop_sign)
        self.assertEqual(NeoSektorSortState.query.one().stop_sign_version,0)
