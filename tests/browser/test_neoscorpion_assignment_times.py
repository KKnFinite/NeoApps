"""Real Dispatch assignment clicks, cross-session refresh and read-only mobile."""
import json
import re
import threading
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from app.extensions import db
from app.models import NeoScorpionFuelAuditEntry, NeoScorpionFuelingEvent, SortDateParkingAssignment
from app.services import neoscorpion as fuel
from tests import test_neoscorpion_assignment_times as cases


class AssignmentTimesBrowserTest(unittest.TestCase):
    def test_chromium(self): self.exercise('chromium')
    def test_webkit(self): self.exercise('webkit')

    def exercise(self, engine):
        case=cases.AssignmentTimesTest();case.setUp();self.addCleanup(case.doCleanups)
        app=case.client.application
        app.config.update(LIVE_SCREEN_REFRESH_INTERVAL_MS=1000,GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=False,
                          GOOGLE_MOTHERBRAIN_READER_ENABLED=False,FLIGHT_API_ENABLED=False)
        plan,child=case.handoff()
        fuel.confirm_fueler_truck_on(case.gateway,case.fueler,case.assignment.id,expected_cycle=1,
            expected_tail='N491UP',expected_truck_id=child.physical_truck_id);db.session.commit()
        mission=case.fixture._mission('UPS902','N492UP',25000,2)
        case.fixture._tail_fuel(mission,inbound_lbs=12000);assignment=case.fixture._assignment(mission)
        truck=case.fixture._nightly_truck('BROWSER');replacement=case.fixture._nightly_truck('REPLACE')
        db.session.add(SortDateParkingAssignment(sort_date_operation_id=case.operation.id,tail_number='N492UP',
            ramp_code='C',position_code='07',lane_number=1));db.session.commit()
        work_ids=[w.id for w in case.row()['console_visits']]
        main_id=case.mission.id;mid=mission.id;aid=assignment.id
        expected_child=case.row()['assignment_receipt'][1]['utc']
        inventory=case.selection.current_gallons;events=NeoScorpionFuelingEvent.query.count()
        server=make_server('127.0.0.1',0,app,threaded=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        origin=f'http://127.0.0.1:{server.server_port}'
        evidence=Path('instance/browser-evidence/assignment-times');evidence.mkdir(parents=True,exist_ok=True)
        metrics=[];errors=[]
        def login(page):
            page.goto(origin+'/login');page.locator('[name=email]').fill(case.dispatcher.email)
            page.locator('[name=password]').fill('TestPassword123!');page.locator('button[type=submit]').click()
            page.wait_for_url(lambda u:'/login' not in u)
            page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
            page.goto(origin+'/neoscorpion/fuel-dispatch');page.evaluate('document.fonts.ready')
        def row(page,mission_id):
            return page.locator(f'.neoscorpion-dispatch-primary-row[data-dispatch-mission-id="{mission_id}"]')
        def detail(page,mission_id):
            button=row(page,mission_id).locator('[data-neoscorpion-dispatch-details]')
            if button.get_attribute('aria-expanded')!='true':button.click()
            return page.locator('#'+button.get_attribute('aria-controls'))
        def choose(page,kind,value):
            control=row(page,mid).locator(f'select[name="{kind}"]').locator('..')
            control.locator('.neoscorpion-dispatch-combobox-trigger').click()
            control.locator(f'[role=option][data-value="{value}"]').click()
        def update(page):
            with page.expect_response(lambda r:'/assignment' in r.url and r.request.method=='POST') as response:
                row(page,mid).locator('[data-dispatch-assignment-submit]').click()
            self.assertEqual(response.value.status,200,response.value.text())
            self.assertTrue(response.value.json()['ok'])
            expect(row(page,mid).locator('[data-dispatch-assignment-submit]')).to_be_enabled()
        try:
            with sync_playwright() as pw:
                browser=getattr(pw,engine).launch()
                try:
                    context=browser.new_context(viewport={'width':1440,'height':1000},service_workers='block')
                    page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));login(page)
                    active=detail(page,main_id)
                    expect(active.locator(f'[data-dispatch-assignment-times] [data-assignment-resource="truck:{child.physical_truck_id}"] time')).to_have_attribute('datetime',expected_child)
                    for wid in work_ids:expect(active.locator(f'[data-visit-assignment-times="{wid}"]')).to_be_visible()
                    # Actual custom combobox clicks and UPDATE create receipts.
                    choose(page,'assigned_fueler_user_id',case.fueler.id);choose(page,'assigned_truck_id',truck.id)
                    update(page)
                    current=detail(page,mid)
                    sent=current.locator('[data-dispatch-assignment-times]')
                    expect(sent.locator('time')).to_have_count(2)
                    initial=sent.locator(f'[data-assignment-resource="truck:{truck.id}"] time').get_attribute('datetime')
                    db.session.expire_all()
                    audit=NeoScorpionFuelAuditEntry.query.filter_by(fuel_assignment_id=aid,field_name='assigned_truck_id').one()
                    self.assertEqual(initial,audit.created_at.isoformat()+'Z')
                    self.assertEqual(audit.changed_by_user_id,case.dispatcher.id)
                    before=NeoScorpionFuelAuditEntry.query.count();update(page);db.session.expire_all()
                    self.assertEqual(NeoScorpionFuelAuditEntry.query.count(),before)
                    expect(detail(page,mid).locator(f'[data-assignment-resource="truck:{truck.id}"] time').first).to_have_attribute('datetime',initial)
                    # An independent Dispatcher changes this same mission while
                    # this browser retains an unsaved, focused Tail Notes draft.
                    notes=current.locator('[data-console-section="tail-notes"]')
                    if not notes.evaluate('e=>e.open'):notes.locator('summary').click()
                    draft=notes.locator('textarea');draft.fill('Keep this assignment timestamp draft');draft.focus()
                    other=browser.new_context(viewport={'width':1440,'height':1000},service_workers='block')
                    colleague=other.new_page();colleague.on('pageerror',lambda e:errors.append(str(e)));login(colleague)
                    choose(colleague,'assigned_truck_id',replacement.id);update(colleague)
                    live=detail(page,mid).locator('[data-dispatch-assignment-times]')
                    expect(live.locator(f':scope > [data-assignment-resource="truck:{replacement.id}"] time')).to_have_attribute('datetime',re.compile(r'.+Z'),timeout=15000)
                    expect(draft).to_have_value('Keep this assignment timestamp draft');expect(draft).to_be_focused()
                    earlier=live.locator('details');earlier.locator('summary').click()
                    expect(earlier.locator(f'[data-assignment-resource="truck:{truck.id}"] time')).to_have_attribute('datetime',initial)
                    other.close()
                    for width in (1440,1024):
                        page.set_viewport_size({'width':width,'height':1000})
                        for board in (False,True):
                            if page.locator('body').evaluate('e=>e.classList.contains("operational-board-view")')!=board:
                                page.locator('[data-operational-board-toggle]:visible').click()
                            target=detail(page,mid)
                            geometry=target.evaluate('e=>({width:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth+1,receipts:[...e.querySelectorAll("[data-dispatch-assignment-times] time")].map(t=>({text:t.textContent,rect:t.getBoundingClientRect().toJSON()}))})')
                            self.assertFalse(geometry['overflow'],geometry)
                            self.assertTrue(all(r['rect']['width']>0 for r in geometry['receipts']),geometry)
                            metrics.append(dict(board=board,**geometry))
                            page.screenshot(path=str(evidence/f'{engine}-{width}-{"board" if board else "normal"}.png'),full_page=True)
                    mobile=browser.new_context(viewport={'width':390,'height':844},service_workers='block')
                    phone=mobile.new_page();phone.on('pageerror',lambda e:errors.append(str(e)));login(phone)
                    for width,height in ((390,844),(375,568)):
                        phone.set_viewport_size({'width':width,'height':height})
                        for mission_id in (mid,main_id):
                            button=row(phone,mission_id).locator('[data-neoscorpion-mobile-details]')
                            if button.get_attribute('aria-expanded')!='true':button.click()
                            target=phone.locator('#'+button.get_attribute('aria-controls')).locator('.scorpion-mobile-readonly')
                            expect(target.locator('[data-mobile-assignment-times] time').first).to_be_visible()
                            if mission_id==mid:
                                history=target.locator('details').filter(has=phone.locator('summary',has_text='Assignment history'))
                                if not history.evaluate('e=>e.open'):history.locator('summary').click()
                                expect(history.locator(f'[data-assignment-resource="truck:{truck.id}"] time')).to_have_attribute('datetime',initial)
                            self.assertEqual(target.locator('input:visible,select:visible,textarea:visible,button[type=submit]:visible').count(),0)
                        self.assertFalse(phone.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                        phone.screenshot(path=str(evidence/f'{engine}-{width}-mobile.png'),full_page=True)
                    self.assertEqual(errors,[])
                    db.session.expire_all();self.assertEqual(case.selection.current_gallons,inventory)
                    self.assertEqual(NeoScorpionFuelingEvent.query.count(),events)
                    metrics.append(dict(initial_utc=initial,child_planned_utc=expected_child,errors=errors))
                    (evidence/f'{engine}-measurements.json').write_text(json.dumps(metrics,indent=2))
                finally:browser.close()
        finally:server.shutdown();server.server_close();thread.join(timeout=5)


if __name__=='__main__':unittest.main()
