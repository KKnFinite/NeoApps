"""Real computed warning styles and cross-device updates, on isolated fuel data."""
import json
import re
from copy import deepcopy
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from app.extensions import db
from app.models import NeoScorpionFuelingEvent, SortDateParkingAssignment
from app.services import neoscorpion as fuel
from tests import test_neoscorpion_dispatch_details as cases
from tests.browser.test_neoscorpion_number_alignment import MEASURE

EVIDENCE=Path('instance/browser-evidence/fuel-comparison')
RED='rgb(255, 98, 107)'


class FuelComparisonBrowserTest(unittest.TestCase):
    def test_chromium_comparisons_and_live_updates(self):self.exercise('chromium')
    def test_webkit_comparisons_and_live_updates(self):self.exercise('webkit')

    def exercise(self,engine):
        case=cases.DetailsTest();case.setUp();self.addCleanup(case.doCleanups);app=case.client.application
        app.config.update(LIVE_SCREEN_REFRESH_INTERVAL_MS=0,GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=False,
                          GOOGLE_MOTHERBRAIN_READER_ENABLED=False,FLIGHT_API_ENABLED=False)
        case.prepare();case.save(case.start+timedelta(minutes=1),**case.final_data());case.off();case.submit()
        fuel.complete_fueled_assignment(case.gateway,case.dispatcher,case.assignment.id);db.session.commit()
        event=NeoScorpionFuelingEvent.query.one()
        event_snapshot=deepcopy({column.name:getattr(event,column.name) for column in event.__table__.columns})
        mission=case.fixture._mission('UPS902','N492UP',25400,2)
        mission.planned_datetime_utc=datetime.utcnow()+timedelta(hours=2)
        case.fixture._tail_fuel(mission,inbound_lbs=12000)
        assignment=case.fixture._assignment(mission,case.truck)
        assignment.assigned_fueler_user_id=case.fueler.id
        empty=case.fixture._mission('UPS903','N493UP',40000,3)
        empty.planned_datetime_utc=datetime.utcnow()+timedelta(hours=3)
        case.fixture._tail_fuel(empty,inbound_lbs=12000);case.fixture._assignment(empty)
        for tail in ('N492UP','N493UP'):
            db.session.add(SortDateParkingAssignment(sort_date_operation_id=case.operation.id,tail_number=tail,
                ramp_code='C',position_code='07' if tail=='N492UP' else '08',lane_number=1))
        db.session.commit()
        work=fuel.save_fueler_entry(case.gateway,case.fueler,dict(assignment_id=assignment.id,
            apu_running='no',remaining_left='6',remaining_ctr='0',remaining_right='6',
            actual_left='15',actual_ctr='0',actual_right='13.75')).fuel_work_state
        case.settings.fuel_density_lbs_per_gallon=6.7;db.session.commit()
        server=make_server('127.0.0.1',0,app,threaded=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        origin=f'http://127.0.0.1:{server.server_port}'
        EVIDENCE.mkdir(parents=True,exist_ok=True);metrics=[];errors=[]
        def login(page,user):
            page.goto(origin+'/login');page.locator('[name=email]').fill(user.email)
            page.locator('[name=password]').fill('TestPassword123!');page.locator('button[type=submit]').click()
            page.wait_for_url(lambda u:'/login' not in u)
            page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
        try:
            with sync_playwright() as pw:
                browser=getattr(pw,engine).launch()
                try:
                    context=browser.new_context(viewport={'width':1440,'height':1000},service_workers='block')
                    page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));login(page,case.dispatcher)
                    def row(mid):return page.locator(f'.neoscorpion-dispatch-primary-row[data-dispatch-mission-id="{mid}"]')
                    def check(mid,shortfall,excess):
                        numbers=[row(mid).locator('[data-dispatch-neo-fuel]'),row(mid).locator('[data-dispatch-required-fuel]')]
                        result=[]
                        for index,number in enumerate(numbers):
                            warning=re.compile(r'\bis-neo-fuel-shortfall\b')
                            if shortfall:expect(number).to_have_class(warning,timeout=8000)
                            else:expect(number).not_to_have_class(warning,timeout=8000)
                            style=number.evaluate('e=>({color:getComputedStyle(e).color,weight:getComputedStyle(e).fontWeight,short:e.classList.contains("is-neo-fuel-shortfall"),excess:e.classList.contains("is-neo-fuel-excess")})')
                            self.assertEqual(style['short'],shortfall,(mid,index,style))
                            self.assertEqual(style['excess'],excess if index==0 else False,(mid,index,style))
                            red=shortfall or (index==0 and excess)
                            self.assertEqual(style['color']==RED,red,(mid,index,style))
                            if red:self.assertEqual(style['weight'],'900')
                            result.append(style)
                        geometry=row(mid).evaluate_all(MEASURE)[0]
                        self.assertLessEqual(max(v['baseline'] for v in geometry['values'])-min(v['baseline'] for v in geometry['values']),1,geometry)
                        self.assertFalse(geometry['overflows'],geometry)
                        self.assertAlmostEqual(geometry['height'],98,delta=1)
                        return result
                    for name,delta,threshold,shortfall,excess in (
                        ('below-100',-100,500,True,False),('below-one-pound',-1,500,True,False),
                        ('equal',0,500,False,False),('below-excess-threshold',3349,500,False,False),
                        ('exact-excess-threshold',3350,500,False,True),('above-excess-threshold',3351,500,False,True),
                        ('custom-700',3350,700,False,False),('custom-300',2010,300,False,True),
                        ('missing-required',None,500,False,False)):
                        mission.planned_fuel_load=28750-delta if delta is not None else None
                        case.mission.planned_fuel_load=30000-delta if delta is not None else None
                        case.settings.neo_fuel_excess_alert_gallons=threshold;db.session.commit()
                        page.goto(origin+'/neoscorpion/fuel-dispatch');page.evaluate('document.fonts.ready')
                        for width in (1440,1280):
                            page.set_viewport_size({'width':width,'height':1000})
                            for board in (False,True):
                                if page.locator('body').evaluate('e=>e.classList.contains("operational-board-view")')!=board:
                                    page.locator('[data-operational-board-toggle]:visible').click()
                                result=[check(mid,shortfall,excess) for mid in (mission.id,case.mission.id)]
                                check(empty.id,False,False)
                                metrics.append(dict(state=name,width=width,board=board,styles=result))
                                if width==1440 and name in ('below-100','exact-excess-threshold'):
                                    page.locator('.neoscorpion-dispatch-table--compact').screenshot(path=str(EVIDENCE/f'{engine}-{name}-{"board" if board else "normal"}.png'))
                    # Both forms of incomplete evidence must remain neutral.
                    tank=next(t for t in work.tank_states if t.tank_code=='left')
                    for missing in ('actual','apu'):
                        tank.actual_lbs=None if missing=='actual' else 15000
                        work.apu_running=None if missing=='apu' else False
                        mission.planned_fuel_load=40000;db.session.commit()
                        page.goto(origin+'/neoscorpion/fuel-dispatch');check(mission.id,False,False)
                    # Cross-device Fueler saves while another mission has a
                    # focused unsaved note: critical styling must still update.
                    tank.actual_lbs=15000;work.apu_running=False;mission.planned_fuel_load=30000
                    case.settings.neo_fuel_excess_alert_gallons=500;case.mission.planned_fuel_load=25000;db.session.commit()
                    app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=1000
                    page.set_viewport_size({'width':1440,'height':1000});page.goto(origin+'/neoscorpion/fuel-dispatch')
                    if page.locator('body').evaluate('e=>e.classList.contains("operational-board-view")'):
                        page.locator('[data-operational-board-toggle]:visible').click()
                    toggle=row(empty.id).locator('[data-neoscorpion-dispatch-details]');toggle.click()
                    details=page.locator('#'+toggle.get_attribute('aria-controls'))
                    details.locator('[data-console-section="tail-notes"] > summary').click()
                    draft=details.locator('[data-tail-note-form] textarea');draft.fill('Keep this unsaved note through fuel comparison changes');draft.focus()
                    mobile=browser.new_context(viewport={'width':390,'height':844},service_workers='block')
                    operator=mobile.new_page();operator.on('pageerror',lambda e:errors.append(str(e)));login(operator,case.fueler)
                    operator.goto(origin+'/neoscorpion/fueler')
                    card=operator.locator(f'[data-fuel-data-card][data-physical-work-id="{work.id}"]')
                    card.locator('[name=actual_right]').fill('15')
                    card.get_by_role('button',name='Save Fuel Entry',exact=True).click()
                    expect(row(mission.id).locator('[data-dispatch-neo-fuel]')).to_have_text('30.0',timeout=12000)
                    check(mission.id,False,False);expect(draft).to_be_focused()
                    expect(draft).to_have_value('Keep this unsaved note through fuel comparison changes')
                    planning=card.locator('[data-fuel-planning-form]')
                    planning.locator('select[name=apu_running]').select_option('yes')
                    planning.locator('[name=apu_source_tank_code]').select_option('left')
                    planning.locator('[name=apu_override_allowance]').fill('0.1')
                    planning.locator('[data-apu-use-manual]').click()
                    card.get_by_role('button',name='Save Fuel Entry',exact=True).click()
                    expect(row(mission.id).locator('[data-dispatch-neo-fuel]')).to_have_text('29.9',timeout=12000)
                    expect(row(mission.id).locator('[data-dispatch-apu-effective]')).to_have_text('APU: 0.1')
                    check(mission.id,True,False);expect(draft).to_be_focused()
                    page.locator('.neoscorpion-dispatch-table--compact').screenshot(path=str(EVIDENCE/f'{engine}-live-apu-shortfall.png'))
                    planning.locator('select[name=apu_running]').select_option('no')
                    card.get_by_role('button',name='Save Fuel Entry',exact=True).click()
                    expect(row(mission.id).locator('[data-dispatch-neo-fuel]')).to_have_text('30.0',timeout=12000)
                    expect(row(mission.id).locator('[data-dispatch-apu-effective]')).to_have_text('APU: 0.0')
                    check(mission.id,False,False)
                    required=row(mission.id).locator('[data-dispatch-required-fuel]')
                    for target,shortfall in (('30.1',True),('30.0',False)):
                        expect(required).not_to_have_attribute('data-autosave-saving','true',timeout=12000)
                        required.fill(target);required.press('Tab')
                        expect(required).to_have_attribute('data-saved-value',target,timeout=12000)
                        expect(required).not_to_have_attribute('data-autosave-saving','true',timeout=12000)
                        check(mission.id,shortfall,False)
                        expect(draft).to_have_value('Keep this unsaved note through fuel comparison changes')
                    db.session.expire_all()
                    self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
                    event=NeoScorpionFuelingEvent.query.one()
                    self.assertEqual({column.name:getattr(event,column.name) for column in event.__table__.columns},event_snapshot)
                    self.assertEqual(case.selection.current_gallons,7000)
                    self.assertIsNone(assignment.completed_at_utc);self.assertIsNone(work.manual_on_at_utc)
                    self.assertEqual(errors,[])
                finally:browser.close()
        finally:
            (EVIDENCE/f'measurements-{engine}.json').write_text(json.dumps(metrics,indent=2))
            server.shutdown();thread.join(timeout=5);case.doCleanups()
