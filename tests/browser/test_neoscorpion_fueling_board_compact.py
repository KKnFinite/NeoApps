"""Real board geometry/data parity against the pre-compact deployed stylesheet."""
import json
from pathlib import Path
import subprocess
import threading
import unittest
from datetime import timedelta

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from app.extensions import db
from app.services import neoscorpion as fuel
from tests import test_neoscorpion_resume as fixtures


BASELINE = 'd98c9457bfbb41c9176636945faf186a662fc760'
SIZES = ((375, 812), (390, 844), (768, 1024), (1440, 900))


class FuelingBoardCompactBrowserTest(unittest.TestCase):
    def test_chromium(self):
        self.exercise('chromium')

    def test_webkit(self):
        self.exercise('webkit')

    def exercise(self, engine):
        case = fixtures.ResumeTest(); case.setUp(); self.addCleanup(case.doCleanups)
        case.prepare()
        case.fueler.first_name = 'Alexandria'; case.fueler.last_name = 'Long Operational Fueler Name'
        db.session.commit()
        case.save(case.start + timedelta(minutes=1), **case.final_data())
        # Canonical layouts: A300 six tanks incl TT; both seven-tank 747s;
        # empty readings, partial readings, a second three-tank type and unknown type.
        for index, tail in enumerate(('N140UP', 'N512UP', 'N612UP', 'N312UP', 'N212UP')):
            truck = case.fixture._nightly_truck('415710' + str(index))
            mission = case.fixture._mission('UPS100' + str(index), tail, 40000, index + 3,
                                           destination='LONG DESTINATION')
            assignment = case.fixture._assignment(mission, truck)
            assignment.assigned_fueler_user_id = case.fueler.id
            db.session.commit()
            if tail == 'N140UP':
                fuel.save_fueler_entry(case.gateway, case.fueler, dict(assignment_id=assignment.id,
                    remaining_l_out='1.1', remaining_l_in='8', remaining_ctr='0',
                    remaining_r_in='8', remaining_r_out='1.1', remaining_tt='0', apu_running='no'))
                db.session.commit()
        app = case.client.application
        app.config.update(LIVE_SCREEN_REFRESH_INTERVAL_MS=5000,GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=False,
                          GOOGLE_MOTHERBRAIN_READER_ENABLED=False,FLIGHT_API_ENABLED=False)
        before_css = subprocess.check_output(['git','show',BASELINE + ':app/static/css/26-neoscorpion.css'])
        server = make_server('127.0.0.1',0,app,threaded=True)
        thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        evidence = Path('instance/browser-evidence/fueling-board-compact'); evidence.mkdir(parents=True,exist_ok=True)
        results = {}; errors = []
        try:
            with sync_playwright() as pw:
                browser = getattr(pw,engine).launch()
                try:
                    context = browser.new_context(service_workers='block')
                    page = context.new_page(); page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(origin+'/login'); page.locator('[name=email]').fill(case.dispatcher.email)
                    page.locator('[name=password]').fill('TestPassword123!'); page.locator('button[type=submit]').click()
                    page.wait_for_url(lambda u:'/login' not in u)
                    def ready():
                        page.goto(origin+'/neoscorpion/fueling-board')
                        page.evaluate('document.fonts.ready'); expect(page.locator('[data-board-work-key]')).to_have_count(6)
                    def heights():
                        return page.locator('[data-board-work-key]').evaluate_all('els=>Object.fromEntries(els.map(el=>[el.dataset.boardWorkKey,Math.round(el.getBoundingClientRect().height*10)/10]))')
                    def data():
                        return page.locator('[data-board-work-key]').evaluate_all('els=>els.map(el=>({key:el.dataset.boardWorkKey,text:el.textContent.replace(/\\s+/g," ").trim()}))')
                    for width,height in SIZES:
                        page.set_viewport_size({'width':width,'height':height})
                        page.route('**/static/css/26-neoscorpion.css?*',lambda route:route.fulfill(body=before_css,content_type='text/css'))
                        ready(); before = heights(); original = data()
                        page.screenshot(path=str(evidence/f'{engine}-{width}-before.png'),full_page=True)
                        page.locator('[data-board-assignment-id="2"]').screenshot(path=str(evidence/f'{engine}-{width}-card-before.png'))
                        page.unroute('**/static/css/26-neoscorpion.css?*')
                        ready(); after = heights()
                        self.assertEqual(original,data())  # Every identity, label, warning and value survives.
                        results[str(width)] = {'before':before,'after':after}
                        page.screenshot(path=str(evidence/f'{engine}-{width}-after.png'),full_page=True)
                        page.locator('[data-board-assignment-id="2"]').screenshot(path=str(evidence/f'{engine}-{width}-card-after.png'))
                        self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                        self.assertEqual(page.locator('[data-board-work-key] input,[data-board-work-key] button').count(),0)
                        for table in page.locator('.neoscorpion-readonly-tanks').all():
                            self.assertEqual(table.locator('[role=columnheader]').all_text_contents(),['Tank','Remaining','Planned','Actual'])
                            # Each tank and its three values share one row; all rows use identical column centers.
                            geometry = table.locator('[role=row]').evaluate_all('''els=>els.map(el=>Array.from(el.children).map(c=>{const r=c.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2,right:r.right,left:r.left,clipped:c.scrollWidth>c.clientWidth+1};}))''')
                            for cells in geometry:
                                self.assertLess(max(c['y'] for c in cells)-min(c['y'] for c in cells),2)
                                self.assertFalse(any(c['clipped'] for c in cells),cells)
                                self.assertTrue(all(cells[i]['right'] <= cells[i+1]['left']+1 for i in range(3)),cells)
                                for index,cell in enumerate(cells):
                                    self.assertAlmostEqual(cell['x'],geometry[0][index]['x'],delta=1)
                        # Ordinary and all configured aircraft cards reach the mobile target.
                        if width < 500:
                            configured = page.locator('[data-board-work-key]:has(.neoscorpion-readonly-tanks)')
                            for card in configured.all():
                                key=card.get_attribute('data-board-work-key')
                                self.assertLessEqual(after[key],before[key]*.60,(engine,width,key,before[key],after[key]))
                    # Actual save -> revision poll -> correct same physical card, no manual reload.
                    page.set_viewport_size({'width':390,'height':844}); ready()
                    target = page.locator(f'[data-board-assignment-id="{case.assignment.id}"]')
                    expect(target.locator('[data-tank-code=left] [role=cell]').last).to_have_text('15.0')
                    old_key = target.get_attribute('data-board-work-key')
                    case.save(case.start+timedelta(minutes=2),actual_left='16')
                    expect(target.locator('[data-tank-code=left] [role=cell]').last).to_have_text('16.0',timeout=15000)
                    self.assertEqual(target.get_attribute('data-board-work-key'),old_key)
                    self.assertEqual(page.locator('[data-board-work-key]').count(),6)
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                    case.off(); case.submit()
                    expect(target.locator('[data-fuel-status]')).to_have_attribute('data-fuel-status','off',timeout=15000)
                    self.assertEqual(target.get_attribute('data-board-work-key'),old_key)
                    expect(target.locator('[data-tank-code=left] [role=cell]').last).to_have_text('16.0')
                    self.assertEqual(errors,[])
                finally:
                    browser.close()
        finally:
            (evidence/f'{engine}-measurements.json').write_text(json.dumps(results,indent=2))
            server.shutdown(); thread.join(timeout=5)
