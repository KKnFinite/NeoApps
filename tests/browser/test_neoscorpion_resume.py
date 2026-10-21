"""Real mobile ON/OFF/resume and independent desktop verification."""
import threading
import unittest
from pathlib import Path
from datetime import timedelta
from playwright.sync_api import sync_playwright,expect
from werkzeug.serving import make_server
from app.extensions import db
from app.models import NeoScorpionFuelWorkState,NeoScorpionFuelingEvent
from tests.test_neoscorpion_resume import ResumeTest


class ResumeBrowserTest(unittest.TestCase):
    def test_mobile_resume_keeps_visit_readings_and_dispatch_complete(self):
        case=ResumeTest();case.setUp();app=case.client.application
        app.config.update(LIVE_SCREEN_REFRESH_INTERVAL_MS=1000,GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=False,
            GOOGLE_MOTHERBRAIN_READER_ENABLED=False,FLIGHT_API_ENABLED=False)
        case.prepare();case.save(case.start+timedelta(minutes=1),notes='Original visit stays',**case.final_data());db.session.commit()
        wid=case.work.id;aid=case.assignment.id
        server=make_server('127.0.0.1',0,app,threaded=True);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        origin=f'http://127.0.0.1:{server.server_port}';evidence=Path('instance/browser-evidence/resume');evidence.mkdir(parents=True,exist_ok=True)
        errors=[]
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch()
                try:
                    mobile=browser.new_context(viewport={'width':390,'height':844},service_workers='block')
                    desktop=browser.new_context(viewport={'width':1440,'height':900},service_workers='block')
                    def login(context,user,path):
                        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                        page.goto(origin+'/login');page.locator('[name=email]').fill(user.email);page.locator('[name=password]').fill('TestPassword123!')
                        page.locator('button[type=submit]').click();page.wait_for_url(lambda u:'/login' not in u)
                        page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                        page.goto(origin+path);return page
                    dispatcher=login(desktop,case.dispatcher,'/neoscorpion/fuel-dispatch')
                    page=login(mobile,case.fueler,'/neoscorpion/fueler')
                    card=page.locator(f'[data-fuel-data-card][data-physical-work-id="{wid}"]')
                    card.locator('.neoscorpion-fueler-off-form button').click()
                    expect(card.locator('[data-resume-fueling-form]')).to_be_visible()
                    card.screenshot(path=str(evidence/'mobile-premature-off.png'))
                    page.reload();card.locator('[data-resume-fueling-form] button').click()
                    expect(card.locator('.neoscorpion-truck-on-row')).to_contain_text('CONFIRMED')
                    expect(card.locator('[data-resume-fueling-form]')).to_have_count(0)
                    expect(card.locator('[name=remaining_left]')).to_have_value('6.0')
                    expect(card.locator('[name=actual_left]')).to_have_value('15.0')
                    expect(card.locator('[name=transfer_fuel_gallons]')).to_have_value('3000')
                    expect(card.locator('[name=notes]')).to_have_value('Original visit stays')
                    self.assertEqual(page.locator('[data-fuel-data-card]').count(),1)
                    page.reload();card.locator('.neoscorpion-fueler-off-form button').click()
                    card.locator('[data-fueler-submit-form] button').click()
                    expect(card.locator('[data-fuel-data-status]')).to_contain_text('SUBMITTED',timeout=10000)
                    row=dispatcher.locator(f'[data-dispatch-mission-id="{case.mission.id}"]')
                    complete=row.get_by_role('button',name='COMPLETE',exact=True)
                    expect(complete).to_be_visible(timeout=10000)
                    row.screenshot(path=str(evidence/'dispatcher-ready-complete.png'))
                    complete.click()
                    expect(row).to_have_class(__import__('re').compile('is-complete'),timeout=10000)
                    db.session.expire_all();self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
                    self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
                    self.assertEqual(case.selection.current_gallons,7000)
                    self.assertEqual(errors,[])
                finally:browser.close()
        finally:server.shutdown();thread.join(timeout=5);case.doCleanups()
