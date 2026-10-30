"""Measured pad/parking/Remote geometry and real CLEAR actions on isolated data."""
import json
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import (Gateway,MotherBrainParkingSettings,SortDateMission,SortDateOperation,
    SortDateParkingAssignment,SortDateTailState,NeoSubZeroDepartureDeiceEvent)
from tests.browser import test_neosubzero_ucc as fixture_module


class UccPadGeometryBrowserTest(unittest.TestCase):
    login=fixture_module.UccBrowserTest.login
    ready=fixture_module.UccBrowserTest.ready
    tearDownClass=classmethod(fixture_module.UccBrowserTest.tearDownClass.__func__)

    @classmethod
    def setUpClass(cls):
        fixture_module.UccBrowserTest.setUpClass.__func__(cls)
        with cls.app.app_context():
            fixture=fixture_module.NeoSubZeroUccTest()
            fixture.operation=db.session.get(SortDateOperation,cls.operation_id)
            cls.queue_ids=cls.missions[:3]
            for index in range(2):
                mission=fixture._departure(f'UPS779{index}',f'N779{index}UP','DEN',4)
                mission.actual_block_out_datetime_utc=datetime(2026,10,23,5,3+index)
                db.session.add(SortDateParkingAssignment(sort_date_operation_id=cls.operation_id,tail_number=mission.assigned_tail_number,ramp_code='A',position_code=f'A0{6+index}'))
                fixture._deice_event(mission,'deice_planned');cls.queue_ids.append(mission.id)
            parked=fixture._departure('UPS7789','N-PARK-TEN','SDF',3)
            db.session.add(SortDateParkingAssignment(sort_date_operation_id=cls.operation_id,tail_number=parked.assigned_tail_number,ramp_code='A',position_code='A10'))
            cls.parked_id=parked.id
            for position in ('R04','R03','R02','R01'):
                mission=fixture._departure('UPS77'+position[1:],f'N-{position}-UP','DEN',4)
                db.session.add(SortDateParkingAssignment(sort_date_operation_id=cls.operation_id,tail_number=mission.assigned_tail_number,ramp_code='R',position_code=position))
            db.session.add(SortDateTailState(sort_date=fixture.operation.sort_date,gateway_code='RFD',sort_name='night',tail_number='N-UNRESOLVED',parking_position='MotherBrain'))
            for index,mission in enumerate(SortDateMission.query.filter_by(sort_date_operation_id=cls.operation_id)):
                mission.planned_datetime_utc=datetime(2026,10,23,8,index)
                mission.planned_datetime_local=datetime(2026,10,23,3,index)
            db.session.commit()
        card=deepcopy(cls.weather_data['future_forecast'][0])
        cls.weather_data['future_forecast']=[{**card,'date_label':f'UPCOMING FIXTURE {i+1}'} for i in range(3)]

    def finish_fixture(self,mid):
        event=NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=mid).one()
        event.status='finished';event.configured_at_utc=datetime(2026,10,23,5,1)
        event.treatment_plan='one_type_i';event.pass1_surface_area='wings_only'
        event.pass1_started_at_utc=datetime(2026,10,23,5,1);event.pass1_ended_at_utc=datetime(2026,10,23,5,3)

    def test_geometry_settings_live_queue_forecast_and_clear(self):
        evidence=Path('instance/browser-evidence/subzero-pad');evidence.mkdir(parents=True,exist_ok=True)
        measurements=[]
        for engine in ('chromium','webkit'):
            with self.app.app_context():
                MotherBrainParkingSettings.query.one().include_throat_default=True
                for mid in self.queue_ids:
                    event=NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=mid).one()
                    event.status='deice_planned';event.configured_at_utc=None
                    event.treatment_plan=None;event.pass1_started_at_utc=None;event.pass1_ended_at_utc=None
                self.finish_fixture(self.queue_ids[0]);db.session.commit()
            browser=getattr(self.pw,engine).launch()
            context=browser.new_context(viewport={'width':1440,'height':1000},service_workers='block')
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            self.login(page);self.ready(page,'/neosubzero/ucc')
            alpha=page.locator('[data-ucc-ramp="Alpha"]')
            for width,height in ((320,900),(375,900),(390,900),(430,900),(768,1024),(1366,768),(1920,900),(1920,1080),(3840,2160)):
                page.set_viewport_size({'width':width,'height':height})
                page.wait_for_function('(w)=>document.documentElement.clientWidth===w && document.querySelector("main.content").getBoundingClientRect().width===w',arg=width)
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                self.assertEqual(page.locator('[data-ucc-ramp]').evaluate_all('(els)=>els.map(el=>el.dataset.uccRamp)'),['Alpha','Bravo','Charlie','Delta','Echo','Remote'])
                expect(page.locator('[data-ucc-ramp="MotherBrain"]')).to_have_count(0)
                expect(page.locator('[data-ucc-location-exceptions]')).to_contain_text('N-UNRESOLVED')
                self.assertEqual(page.get_by_text('UPCOMING OPERATIONAL SORT WINDOWS',exact=True).count(),1)
                expect(page.locator('[data-ucc-upcoming]')).to_be_visible()
                if width>900:
                    bounds=page.evaluate("""() => {
                      const r=e=>{const b=e.getBoundingClientRect();return {x:b.x,y:b.y,right:b.right,bottom:b.bottom,width:b.width}};
                      const ramps=[...document.querySelectorAll('[data-ucc-ramp]')];
                      return {ramps:ramps.map(e=>({name:e.dataset.uccRamp,...r(e)})),forecast:r(document.querySelector('[data-ucc-upcoming]')),current:r(document.querySelector('.neosubzero-weather')),fluid:r(document.querySelector('.neosubzero-ucc-fluid'))};
                    }""")
                    measurements.append(dict(engine=engine,viewport=width,**bounds))
                    self.assertEqual(len({round(r['y']) for r in bounds['ramps'][:5]}),1,bounds)
                    self.assertAlmostEqual(bounds['ramps'][5]['x'],bounds['ramps'][0]['x'],delta=1)
                    self.assertGreaterEqual(bounds['ramps'][5]['y'],max(r['bottom'] for r in bounds['ramps'][:5]))
                    self.assertAlmostEqual(bounds['forecast']['x'],bounds['ramps'][1]['x'],delta=1)
                    self.assertAlmostEqual(bounds['forecast']['right'],bounds['ramps'][4]['right'],delta=1)
                    self.assertLessEqual(bounds['current']['bottom'],bounds['fluid']['y'])
                    for prefix,ramp_name in zip('ABCDE',('Alpha','Bravo','Charlie','Delta','Echo')):
                        ramp=page.locator(f'[data-ucc-ramp="{ramp_name}"]')
                        spots={n:ramp.locator(f'[data-ucc-parking="{prefix}{n:02d}"]').bounding_box() for n in range(1,11)}
                        for middle,left,right in ((10,1,5),(9,4,8)):
                            self.assertLess(spots[left]['x'],spots[middle]['x']);self.assertLess(spots[middle]['x'],spots[right]['x'])
                            self.assertGreaterEqual(spots[middle]['y'],spots[left]['y']-1)
                            self.assertLessEqual(spots[middle]['y']+spots[middle]['height'],spots[left]['y']+spots[left]['height']+1)
                        expect(ramp.locator('[data-ucc-deice-pad]')).to_be_visible()
                    for q,left,right in ((1,2,6),(2,4,8)):
                        b=alpha.locator(f'[data-ucc-queue-position="{q}"]').bounding_box()
                        l=alpha.locator(f'[data-ucc-parking="A{left:02d}"]').bounding_box();r=alpha.locator(f'[data-ucc-parking="A{right:02d}"]').bounding_box()
                        self.assertLess(l['x'],b['x']);self.assertLess(b['x'],r['x'])
                        self.assertGreaterEqual(b['y'],l['y']-1);self.assertLessEqual(b['y']+b['height'],l['y']+l['height']+1)
                    pad=alpha.locator('[data-ucc-deice-pad]').bounding_box();park=alpha.locator('[data-ucc-parking="A10"]').bounding_box()
                    self.assertAlmostEqual(pad['y'],park['y'],delta=1)
                    remote=page.locator('[data-ucc-ramp="Remote"]')
                    slots=remote.locator('[data-ucc-parking]').evaluate_all('(els)=>els.map(e=>({code:e.dataset.uccParking,x:e.getBoundingClientRect().x,y:e.getBoundingClientRect().y}))')
                    self.assertEqual([s['code'] for s in slots],['R04','R03','R02','R01']);self.assertEqual(len({round(s['y']) for s in slots}),1)
                    for position in ('R04','R03','R02','R01'):expect(remote.locator(f'[data-ucc-parking="{position}"]')).to_contain_text(f'N-{position}-UP')
                    expect(remote.locator('[data-ucc-deice-pad], [data-ucc-queue-position]')).to_have_count(0)
                    expect(alpha).to_contain_text('DEICE CAPACITY REVIEW')
                    expect(alpha.locator('[data-ucc-aircraft="%s"]'%self.queue_ids[4])).to_be_visible()
                    expect(page.locator('[data-ucc-ramp="Bravo"] [data-ucc-queue-position]')).to_have_count(0)
                else:
                    expect(alpha.locator('.ucc-ramp-diagram')).to_be_hidden()
                    expect(page.locator('[data-ucc-editor="Alpha-1"]')).to_be_visible()
                page.screenshot(path=str(evidence/f'{engine}-{width}.png'))
                if width in (1440,1920):
                    page.locator('.ucc-ramp-board').screenshot(path=str(evidence/f'{engine}-{width}-board.png'),
                        style='.operational-node-topbar { visibility:hidden !important; }')
            page.set_viewport_size({'width':1920,'height':1080})
            page.locator('[data-ucc-tv-enter]').click();page.screenshot(path=str(evidence/f'{engine}-tv.png'))
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
            page.locator('[data-ucc-tv-exit]').click()
            editor=page.locator('[data-ucc-editor="Delta-3"]');editor.locator(':scope > summary').click()
            draft=editor.locator('select[name="person_id"]').first;draft.select_option(str(self.people[2]));draft.focus()
            with self.app.app_context():
                MotherBrainParkingSettings.query.one().include_throat_default=False;db.session.commit()
            expect(page.locator('[data-ucc-parking$="09"], [data-ucc-parking$="10"]')).to_have_count(0,timeout=10000)
            expect(alpha.locator('[data-ucc-queue-position]')).to_have_count(2)
            expect(alpha.locator('[data-ucc-aircraft="%s"]'%self.parked_id)).to_be_visible()
            expect(draft).to_be_focused();expect(draft).to_have_value(str(self.people[2]))
            self.weather_data['future_forecast'][0]['conditions']='LIVE FORECAST EVIDENCE'
            with patch('app.neonodes.neosubzero.routes.neosubzero_weather_revision',return_value='weather-live'):
                expect(page.locator('[data-ucc-upcoming]')).to_contain_text('LIVE FORECAST EVIDENCE',timeout=10000)
            coordinator=context.new_page()
            for index in (0,1):
                if index:
                    with self.app.app_context():self.finish_fixture(self.queue_ids[index]);db.session.commit()
                self.ready(coordinator,f'/neosubzero/coordinator?ramp=Alpha&mission={self.queue_ids[index]}')
                coordinator.locator('button[name="action"][value="clear"]').click()
                expect(alpha.locator('[data-ucc-aircraft="%s"]'%self.queue_ids[index])).to_have_count(0,timeout=10000)
                expect(alpha.locator('[data-ucc-deice-pad] [data-ucc-aircraft]')).to_have_attribute('data-ucc-aircraft',str(self.queue_ids[index+1]),timeout=10000)
            expect(alpha.locator('[data-ucc-queue-position="3"]')).to_have_count(0)
            expect(draft).to_be_focused();expect(draft).to_have_value(str(self.people[2]))
            with self.app.app_context():
                event=NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=self.queue_ids[0]).one()
                self.assertEqual(event.status,'cleared');self.assertEqual(event.pass1_ended_at_utc,datetime(2026,10,23,5,3))
            self.assertFalse(errors,errors);context.close();browser.close()
        (evidence/'measurements.json').write_text(json.dumps(measurements,indent=2))
