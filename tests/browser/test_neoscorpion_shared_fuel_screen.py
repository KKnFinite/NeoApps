"""Real shared Fueler/Dispatcher screens and direct entry; no live integrations."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
from threading import Thread
import unittest
from unittest.mock import patch
from jinja2 import ChoiceLoader, DictLoader
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from app.extensions import db
from app.models import NeoScorpionDirectEntry, NeoScorpionFuelWorkState, NeoScorpionFuelingEvent
from tests import test_neoscorpion_direct_entry as fixtures

BASE='6b88bc4e914e42e5cfd1d3bbd7fd44e68d0afe11'
EVIDENCE=Path('instance/browser-evidence/shared-fuel-screen')


class SharedFuelScreenBrowserTest(unittest.TestCase):
    def exercise(self,engine):
        case=fixtures.DirectEntryTest();case.setUp();app=case.client.application
        app.config.update(LIVE_SCREEN_REFRESH_INTERVAL_MS=0,GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=False,
            GOOGLE_MOTHERBRAIN_READER_ENABLED=False,FLIGHT_API_ENABLED=False)
        EVIDENCE.mkdir(parents=True,exist_ok=True)
        loader=app.jinja_env.loader
        original={'neonodes/neoscorpion/_fueler_card.html':subprocess.check_output([
            'git','show',BASE+':app/templates/neonodes/neoscorpion/_fueler_card.html']).decode()}
        old_css=subprocess.check_output(['git','show',BASE+':app/static/css/26-neoscorpion.css']).decode()
        case.prepare();case.save(case.start+timedelta(minutes=1),**case.final_data())
        case.work.notes='Correct original visit notes';db.session.commit()
        mid,aid,wid=case.mission.id,case.assignment.id,case.work.id
        fresh=case.fixture._mission('UPS-DIRECT','N492UP',25000,1);fresh_id=fresh.id;db.session.commit()
        direct_url=f'/neoscorpion/fuel-dispatch/missions/{fresh_id}/direct-entry'
        server=make_server('127.0.0.1',0,app,threaded=True);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        origin=f'http://127.0.0.1:{server.server_port}';metrics=[];errors=[]
        try:
            with sync_playwright() as pw:
                browser=getattr(pw,engine).launch()
                try:
                    pages={}
                    def login(user):
                        nonlocal page
                        if user.id in pages:
                            page=pages[user.id];return
                        page=browser.new_page(viewport={'width':1440,'height':1000},service_workers='block')
                        pages[user.id]=page
                        def capture_error(error):
                            errors.append(str(error))
                            print('SHARED_BROWSER_ERROR',engine,datetime.now().isoformat(),page.url,str(error),error.stack)
                        page.on('pageerror',capture_error)
                        page.goto(origin+'/login')
                        page.locator('[name=email]').fill(user.email)
                        page.locator('[name=password]').fill('TestPassword123!')
                        page.locator('button[type=submit]').click();page.wait_for_url(lambda u:'/login' not in u)
                    page=None;login(case.dispatcher)
                    page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                    def show_panel(url):
                        page.evaluate("""url=>{const b=document.createElement('button');b.dataset.fuelDataOpen=url;
                            document.body.append(b);b.click();b.remove();}""",url)
                        expect(page.locator('[data-fuel-data-dialog] [data-fuel-data-card]')).to_be_visible()
                        return page.locator('[data-fuel-data-dialog] [data-fuel-data-card]')
                    for phase in ('before','after'):
                        app.jinja_env.loader=ChoiceLoader([DictLoader(original),loader]) if phase=='before' else loader
                        app.jinja_env.cache.clear()
                        if phase=='before':page.route('**/css/26-neoscorpion.css*',lambda r:r.fulfill(body=old_css,content_type='text/css'))
                        else:page.unroute('**/css/26-neoscorpion.css*')
                        page.goto(origin+'/neoscorpion/fuel-dispatch')
                        card=show_panel(f'/neoscorpion/fuel-dispatch/fueler-data/{aid}')
                        for width in (1440,390,375):
                            page.set_viewport_size({'width':width,'height':1000 if width>700 else 844})
                            page.evaluate('document.fonts.ready')
                            metrics.append(dict(engine=engine,phase=phase,width=width,height=card.bounding_box()['height']))
                            page.screenshot(path=str(EVIDENCE/f'{engine}-open-{phase}-{width}.png'),full_page=True)
                            if phase=='after':
                                self.assertFalse(card.evaluate('e=>e.scrollWidth>e.clientWidth+1'))
                                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width+1)
                        if phase=='after':
                            self.assertEqual(card.locator('[name=actual_left]').input_value(),'15.0')
                            self.assertEqual(card.locator('[name=notes]').input_value(),'Correct original visit notes')
                            self.assertEqual(card.locator('[data-truck-on-form],[data-fueler-submit-form]').count(),0)
                            self.assertEqual(card.locator('[data-apu-editor-form]').count(),0)
                            self.assertLess(card.locator('.neoscorpion-fuel-tank-grid').bounding_box()['y'],card.locator('.scorpion-fueler-apu').bounding_box()['y'])
                    page.locator('[data-fuel-data-close]').click()
                    # Actual Fueler page uses the same card, tank rows and APU
                    # section, but retains its own physical workflow actions.
                    login(case.fueler);page.goto(origin+'/neoscorpion/fueler')
                    fuelcard=page.locator(f'[data-fuel-assignment-id="{aid}"]').first
                    expect(fuelcard).to_be_visible();expect(fuelcard.locator('[name=actual_left]')).to_have_value('15.0')
                    self.assertEqual(fuelcard.locator('.scorpion-fueler-mission,.scorpion-fueler-apu').count(),2)
                    self.assertEqual(fuelcard.locator('[data-fueler-submit-form]').count(),1)
                    page.screenshot(path=str(EVIDENCE/f'{engine}-fueler-current-375.png'),full_page=True)
                    login(case.dispatcher);page.set_viewport_size({'width':1440,'height':1000})
                    app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=5000
                    page.goto(origin+'/neoscorpion/fuel-dispatch')
                    observer_context=browser.new_context(storage_state=page.context.storage_state(),
                        viewport={'width':1440,'height':1000},service_workers='block')
                    observer=observer_context.new_page();observer.goto(origin+'/neoscorpion/fuel-dispatch')
                    observer_row=observer.locator(f'[data-dispatch-mission-id="{fresh_id}"]')
                    expect(observer_row.locator('[data-fuel-status]')).not_to_contain_text('COMPLETE')
                    row=page.locator(f'[data-dispatch-mission-id="{fresh_id}"]')
                    row.locator('[data-neoscorpion-dispatch-details]').click()
                    button=page.locator(f'[data-fuel-data-open="{direct_url}"]')
                    expect(button).to_be_visible();button.click()
                    card=page.locator('[data-screen-mode=dispatcher_direct_entry]')
                    expect(card).to_be_visible();expect(card.locator('[data-direct-entry-complete]')).to_be_disabled()
                    self.assertEqual(card.locator('[data-truck-on-form],.neoscorpion-fueler-off-form,[data-fueler-submit-form]').count(),0)
                    for code,value in (('left','14'),('ctr','0'),('right','14')):
                        card.locator('[name=actual_'+code+']').fill(value)
                    expect(card.locator('[data-actual-total]')).to_have_text('28.0')
                    expect(card.locator('[data-neo-fuel-output]')).to_have_text('INCOMPLETE')
                    expect(card.locator('[data-direct-entry-complete]')).to_be_enabled()
                    for width in (1440,390,375):
                        page.set_viewport_size({'width':width,'height':1000 if width>700 else 844})
                        self.assertFalse(card.evaluate('e=>e.scrollWidth>e.clientWidth+1'))
                        page.screenshot(path=str(EVIDENCE/f'{engine}-direct-entry-{width}.png'),full_page=True)
                    # Failed / stale responses retain exact inputs and reset
                    # the button. No automatic retry or false success message.
                    for status,message in ((503,'Synthetic outage'),(409,'Mission changed; draft retained')):
                        page.route('**'+direct_url,lambda r,request,s=status,m=message:r.fulfill(status=s,json={'ok':False,'error':m}),times=1)
                        card.locator('[data-direct-entry-complete]').click()
                        expect(card.locator('[data-fuel-data-status]')).to_have_text(message)
                        expect(card.locator('[name=actual_left]')).to_have_value('14')
                        expect(card.locator('[data-direct-entry-complete]')).to_be_enabled()
                    with app.app_context():self.assertEqual(NeoScorpionDirectEntry.query.count(),0)
                    card.locator('[data-direct-entry-complete]').click()
                    expect(card.locator('[data-fuel-data-status]')).to_contain_text('COMPLETE · Dispatcher direct entry recorded')
                    expect(observer_row.locator('[data-fuel-status]')).to_contain_text('COMPLETE',timeout=15000)
                    observer_context.close()
                    self.assertEqual(card.locator('[data-direct-entry-form]').count(),0)
                    expect(card.locator('[data-actual-total]')).to_have_text('28.0')
                    with app.app_context():
                        record=NeoScorpionDirectEntry.query.one();self.assertEqual(record.sort_date_mission_id,fresh_id)
                        self.assertIsNone(record.snapshot['apu_running']);self.assertIsNone(record.snapshot['transfer_fuel_gallons'])
                        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
                        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
                    with page.expect_response(lambda r:'/fuel-dispatch/live-panel' in r.url and 'completion_only' not in r.url) as close_refresh:
                        page.locator('[data-fuel-data-close]').click()
                    self.assertTrue(close_refresh.value.json()['ok'])
                    page.set_viewport_size({'width':1440,'height':1000})
                    page.goto(origin+'/neoscorpion/fuel-dispatch')
                    row=page.locator(f'[data-dispatch-mission-id="{fresh_id}"]')
                    expect(row.locator('[data-fuel-status]')).to_contain_text('COMPLETE')
                    row.locator('[data-fuel-data-open]').click()
                    expect(page.locator('[data-fuel-data-card] [data-actual-total]')).to_have_text('28.0')
                    page.locator('[data-fuel-data-close]').click()
                    page.locator('body').evaluate('e=>e.classList.add("operational-board-view")')
                    row.locator('[data-fuel-data-open]').click()
                    expect(page.locator('[data-screen-mode=dispatcher_direct_entry]')).to_be_visible()
                    page.screenshot(path=str(EVIDENCE/f'{engine}-completed-board.png'),full_page=True)
                    self.assertFalse(errors,errors)
                    (EVIDENCE/f'{engine}-metrics.json').write_text(json.dumps(metrics,indent=2))
                finally:browser.close()
        finally:
            app.jinja_env.loader=loader;app.jinja_env.cache.clear()
            server.shutdown();thread.join(timeout=5);case.doCleanups()

    def test_chromium(self):self.exercise('chromium')
    def test_webkit(self):self.exercise('webkit')
