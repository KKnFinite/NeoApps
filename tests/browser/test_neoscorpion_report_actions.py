"""Authenticated before/after actions and event-owned HTML/downloaded PDF."""
from datetime import timedelta
from pathlib import Path
import subprocess
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from jinja2 import ChoiceLoader, DictLoader
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from app import create_app
from app.extensions import db
from app.models import NeoScorpionSortAssetState
from app.services import neoscorpion as fuel
from app.services.neoscorpion_assets import record_nightly_operational_change
from tests import test_neoscorpion_dispatch_details as cases
from tests import test_neoscorpion_dispatch_planning as fixtures

EVIDENCE = Path('instance/browser-evidence/report-actions')
BASELINE = '02f7c1be08eeccec53883476ad97099d93a485d3'


class ReportActionsBrowserTest(unittest.TestCase):
    def exercise(self, engine):
        # Browser and fixture writes need distinct connections; a shared
        # in-memory StaticPool can let a GET roll back fixture setup.
        database_path = (Path('instance') / ('scorpion-report-'+uuid4().hex+'.sqlite')).resolve()
        def file_app(config):
            return create_app(type('ReportBrowserConfig',(config,),{
                'SQLALCHEMY_DATABASE_URI':'sqlite:///'+database_path.as_posix()}))
        case = cases.DetailsTest()
        with patch.object(fixtures,'create_app',file_app):case.setUp()
        app = case.client.application
        app.config.update(LIVE_SCREEN_REFRESH_INTERVAL_MS=0,
                          GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=False,
                          GOOGLE_MOTHERBRAIN_READER_ENABLED=False, FLIGHT_API_ENABLED=False)
        loader = app.jinja_env.loader
        old = subprocess.check_output(['git','show',BASELINE+':app/templates/neonodes/neoscorpion/_dispatch_operations.html']).decode()
        old_css = subprocess.check_output(['git','show',BASELINE+':app/static/css/26-neoscorpion.css']).decode()
        server = make_server('127.0.0.1',0,app,threaded=True)
        thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        EVIDENCE.mkdir(parents=True,exist_ok=True)
        errors = []
        try:
            with sync_playwright() as pw:
                browser = getattr(pw,engine).launch()
                try:
                    page = browser.new_page(viewport={'width':1920,'height':900},service_workers='block')
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(origin+'/login');page.locator('[name=email]').fill(case.dispatcher.email)
                    page.locator('[name=password]').fill('TestPassword123!')
                    page.locator('button[type=submit]').click();page.wait_for_url(lambda u:'/login' not in u)
                    page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                    def operations():
                        page.goto(origin+'/neoscorpion/fuel-dispatch')
                        row = page.locator(f'[data-dispatch-mission-id="{case.mission.id}"]')
                        toggle = row.locator('[data-neoscorpion-dispatch-details]')
                        if toggle.get_attribute('aria-expanded')!='true':toggle.click()
                        return page.locator('#'+toggle.get_attribute('aria-controls')).locator('[data-dispatch-operations]')

                    # Current direct entry is still conditional on no prior work.
                    ops = operations()
                    expect(ops.locator('[data-fuel-data-open]')).to_have_count(1)
                    ops.locator('[data-fuel-data-open]').click()
                    expect(page.locator('[data-fuel-data-dialog]')).to_be_visible()
                    page.keyboard.press('Escape')
                    page.goto('about:blank')
                    case.prepare()
                    # Authentic pre-change template/CSS, same fixture and screen.
                    app.jinja_env.loader = ChoiceLoader([DictLoader({'neonodes/neoscorpion/_dispatch_operations.html':old}),loader])
                    app.jinja_env.cache.clear()
                    page.route('**/css/26-neoscorpion.css*',lambda route:route.fulfill(body=old_css,content_type='text/css'))
                    ops=operations();ops.screenshot(path=str(EVIDENCE/f'{engine}-operations-before.png'))
                    app.jinja_env.loader=loader;app.jinja_env.cache.clear();page.unroute('**/css/26-neoscorpion.css*')
                    app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=1000
                    ops=operations()
                    expect(ops.locator('[data-fuel-data-open]')).to_have_count(0)
                    buttons=ops.locator('[data-operation-toggle]')
                    self.assertEqual(buttons.count(),3)
                    self.assertEqual(ops.locator('summary').count(),0)
                    positions=buttons.evaluate_all('es=>es.map(e=>e.getBoundingClientRect().top)')
                    self.assertLessEqual(max(positions)-min(positions),1)
                    ops.screenshot(path=str(EVIDENCE/f'{engine}-operations-after.png'))
                    planner=ops.locator('[data-console-section="second-truck"]')
                    trigger=planner.locator('[data-operation-toggle]');trigger.click()
                    expect(trigger).to_have_attribute('aria-expanded','true')
                    select=planner.locator('[name=proposed_truck_id]')
                    choice=select.locator('option[value]:not([value=""])').first.get_attribute('value')
                    select.select_option(choice)
                    trigger.click();expect(select).to_be_hidden();trigger.click();expect(select).to_have_value(choice)
                    # Failed save is explicit and never loses the existing draft.
                    page.route('**/fuel-dispatch/pending-uplift',lambda r:r.fulfill(status=409,
                        content_type='application/json',body='{"error":"Fixture stale plan; review refreshed state."}'))
                    planner.locator('form button').click()
                    expect(planner.locator('[data-lifecycle-status]')).to_contain_text('Fixture stale plan')
                    expect(select).to_have_value(choice)
                    page.unroute('**/fuel-dispatch/pending-uplift')
                    with page.expect_response(lambda r:'live-panel' in r.url):
                        record_nightly_operational_change(NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=case.operation.id).one(),case.operation.id)
                        db.session.commit()
                    expect(select).to_have_value(choice)
                    expect(trigger).to_have_attribute('aria-expanded','true')
                    expect(planner.locator('[data-lifecycle-status]')).to_contain_text('Fixture stale plan')
                    trigger.focus()
                    with page.expect_response(lambda r:'live-panel' in r.url):
                        record_nightly_operational_change(NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=case.operation.id).one(),case.operation.id)
                        db.session.commit()
                    expect(trigger).to_be_focused()
                    # Unavailable controls reveal their actual reason and no form.
                    uplift=ops.locator('[data-console-section="start-uplift"]')
                    uplift.locator('[data-operation-toggle]').click()
                    expect(uplift.locator('[data-operation-reason]')).to_be_visible()
                    expect(uplift.locator('form')).to_have_count(0)

                    page.goto('about:blank');app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=0
                    case.save(case.start+timedelta(minutes=10),**case.final_data());case.off();case.submit()
                    fuel.complete_fueled_assignment(case.gateway,case.dispatcher,case.assignment.id);db.session.commit()
                    ops=operations()
                    for kind in ('uplift','defuel'):
                        section=ops.locator(f'[data-console-section="start-{kind}"]')
                        button=section.locator('[data-operation-toggle]')
                        if button.get_attribute('aria-expanded')!='true':button.click()
                        form=section.locator('form');expect(form).to_be_visible()
                        expect(form.locator('[name=cycle_type]')).to_have_value(kind)
                        field=form.locator('[name=required_fuel]');field.fill('42.7')
                        button.click();button.click();expect(field).to_have_value('42.7')
                        if kind=='defuel':
                            expect(form.locator('[name=assigned_fueler_user_id]')).to_be_visible()
                            expect(form.locator('[name=assigned_truck_id]')).to_be_visible()
                    for width,height in ((1920,900),(1920,1080),(1366,768),(3840,2160),(1024,768)):
                        page.set_viewport_size({'width':width,'height':height})
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width+1)
                        self.assertEqual(ops.evaluate("e=>[...e.querySelectorAll('button,input:not([type=hidden]),select')].filter(n=>{let b=n.getBoundingClientRect(),p=e.getBoundingClientRect();return b.width&&(b.left<p.left-1||b.right>p.right+1)}).length"),0)
                    ops.screenshot(path=str(EVIDENCE/f'{engine}-expanded-forms.png'))
                    # Preserve existing compact mobile read-only Dispatcher policy.
                    for width in (320,375,390,430,768):
                        page.set_viewport_size({'width':width,'height':900})
                        expect(ops).to_be_hidden()
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width+1)

                    viewer=browser.new_page(viewport={'width':1920,'height':900},service_workers='block')
                    viewer.goto(origin+'/login');viewer.locator('[name=email]').fill(case.fueler.email)
                    viewer.locator('[name=password]').fill('TestPassword123!')
                    viewer.locator('button[type=submit]').click();viewer.wait_for_url(lambda u:'/login' not in u)
                    viewer.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                    viewer.goto(origin+'/neoscorpion/fuel-dispatch')
                    vrow=viewer.locator(f'[data-dispatch-mission-id="{case.mission.id}"]')
                    vtoggle=vrow.locator('[data-neoscorpion-dispatch-details]')
                    if vtoggle.get_attribute('aria-expanded')!='true':vtoggle.click()
                    vops=viewer.locator('#'+vtoggle.get_attribute('aria-controls')).locator('[data-dispatch-operations]')
                    expect(vops.locator('button')).to_have_count(0)
                    expect(vops).to_contain_text('read-only')
                    viewer.close()
                    page.goto(origin+'/neoscorpion/reports/fuel')
                    expect(page.locator('.neoscorpion-report-table thead')).to_contain_text('FOB')
                    expect(page.locator('[data-fueling-event-id]')).to_have_count(1)
                    response=page.request.get(origin+'/neoscorpion/reports/fuel?format=pdf')
                    self.assertEqual(response.status,200)
                    (EVIDENCE/f'{engine}-fuel-report.pdf').write_bytes(response.body())
                    for width in (1920,1366,768,320,375,390,430):
                        page.set_viewport_size({'width':width,'height':900})
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width+1)
                        if width in (1920,390):page.screenshot(path=str(EVIDENCE/f'{engine}-report-{width}.png'),full_page=True)
                        if width==390:
                            page.locator('.neoscorpion-report-table-wrap').evaluate('e=>e.scrollLeft=e.scrollWidth')
                            page.screenshot(path=str(EVIDENCE/f'{engine}-report-mobile-fob.png'),full_page=True)
                    self.assertEqual(errors,[])
                finally:browser.close()
        finally:
            app.jinja_env.loader=loader;app.jinja_env.cache.clear()
            server.shutdown();thread.join(timeout=5);server.server_close();case.doCleanups()
            with app.app_context():db.engine.dispose()
            database_path.unlink(missing_ok=True)

    def test_chromium(self):self.exercise('chromium')
    def test_webkit(self):self.exercise('webkit')
