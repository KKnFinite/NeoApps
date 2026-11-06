"""Compact Coordinator rows and shared live pad projection, using synthetic data."""
import json
import unittest
from pathlib import Path

from playwright.sync_api import expect
from app.extensions import db
from app.models import SortDateMission, SortDateParkingAssignment
from tests.browser import test_neosubzero_ucc as fixtures

EVIDENCE = Path('instance/compact-pass-pad/browser')


class CompactPassPadBrowserTest(unittest.TestCase):
    def exercise(self, engine):
        case = fixtures.UccBrowserTest()
        case.setUpClass()
        browser = None
        measurements = []
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        try:
            with case.app.app_context():
                mission = db.session.get(SortDateMission, case.missions[0])
                if engine == 'webkit':
                    # Also exercise actual START before the separate Block-Out
                    # milestone. No other aircraft claims the recorded A10 stand.
                    for mission_id in case.missions[:3]:
                        db.session.get(SortDateMission, mission_id).actual_block_out_datetime_utc = None
                    SortDateParkingAssignment.query.filter_by(sort_date_operation_id=case.operation_id,
                        tail_number=mission.assigned_tail_number).one().position_code = 'A10'
                db.session.commit()
            browser = getattr(case.pw, engine).launch()
            context = browser.new_context(viewport={'width':390, 'height':844})
            errors = []
            context.on('page', lambda p: p.on('pageerror', lambda e: errors.append(str(e))))
            coordinator = context.new_page()
            case.login(coordinator)
            case.ready(coordinator, f'/neosubzero/coordinator?ramp=Alpha&mission={case.missions[0]}')
            form = coordinator.locator('[data-subzero-deice-form]')
            plan = form.locator('[name="treatment_plan"]')
            plan.select_option('one_type_i')
            expect(form.locator('[data-subzero-pass="1"]')).to_be_visible()
            expect(form.locator('[data-subzero-pass="2"]')).to_be_hidden()
            plan.select_option('type_i_type_iv')
            expect(form.locator('[data-subzero-pass="2"] legend')).to_have_text('PASS 2 · TYPE IV')
            for width in (320,375,390,430,1440):
                coordinator.set_viewport_size({'width':width, 'height':900})
                boxes = form.locator('[data-subzero-pass]').evaluate_all('''passes => passes.map(p => {
                    const box=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,h:r.height}};
                    const css=getComputedStyle(p);
                    return {labels:[...p.querySelectorAll('label')].map(box), controls:[...p.querySelectorAll('input,select,button:not([hidden])')].map(box),
                            h:p.getBoundingClientRect().height,border:css.borderLeftWidth,background:css.backgroundColor};
                })''')
                for row in boxes:
                    self.assertLess(max(b['y'] for b in row['labels'])-min(b['y'] for b in row['labels']),1,boxes)
                    self.assertLess(row['h'],85,boxes)
                    self.assertEqual(row['border'],'0px')
                    for b in row['controls']:
                        self.assertGreaterEqual(b['h'],36)
                        self.assertGreaterEqual(b['x'],0)
                        self.assertLessEqual(b['right'],width)
                self.assertLessEqual(coordinator.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                expect(form.locator('button[type="submit"]')).to_be_visible()
                expect(coordinator.locator('button[value="clear"]')).to_be_visible()
                measurements.append({'width':width,'passes':boxes})
                if width in (390,1440):
                    form.screenshot(path=str(EVIDENCE/f'{engine}-passes-{width}.png'))

            desktop = context.new_page()
            desktop.set_viewport_size({'width':1920, 'height':1080})
            case.ready(desktop, '/neosubzero/ucc')
            tv = context.new_page()
            tv.set_viewport_size({'width':1920, 'height':1080})
            case.ready(tv, '/neosubzero/ucc')
            tv.evaluate('document.documentElement.requestFullscreen=undefined')
            tv.locator('[data-ucc-tv-enter]').click()
            phone = context.new_page()
            phone.set_viewport_size({'width':390, 'height':844})
            case.ready(phone, '/neosubzero/ucc?view=ramp&ramp=Alpha')
            boards = (desktop,tv,phone)
            identity = str(case.missions[0])
            for page in boards:
                expect(page.locator('[data-ucc-ramp="Alpha"] [data-ucc-deice-pad] .ucc-pad-outline')).to_be_visible()
                expect(page.locator('[data-ucc-ramp="Alpha"] [data-ucc-queue-position="1"] [data-ucc-aircraft="'+identity+'"]')).to_have_count(1)

            coordinator.set_viewport_size({'width':390,'height':844})
            form.locator('[data-subzero-deice-stamp="pass1_start"]').click()
            self.assertRegex(form.locator('[name="pass1_start"]').input_value(),r'^\d{2}:\d{2}$')
            form.locator('[name="pass1_surface_area"]').select_option('wings_only')
            form.locator('[name="pass1_start"]').fill('22:10')
            with coordinator.expect_navigation():
                form.locator('button[type="submit"]').click()

            def assert_pad(state):
                for page in boards:
                    ramp = page.locator('[data-ucc-ramp="Alpha"]')
                    expect(ramp.locator('[data-ucc-deice-pad] .state-'+state)).to_be_visible(timeout=10000)
                    expect(ramp.locator('.ucc-ramp-diagram [data-ucc-aircraft="'+identity+'"]')).to_have_count(1)
                    expect(ramp.locator('[data-ucc-queue-position] [data-ucc-aircraft="'+identity+'"]')).to_have_count(0)
                    expect(ramp.locator('[data-ucc-queue-position]')).to_have_count(4)
                    expect(ramp.locator('.ucc-ramp-diagram')).not_to_contain_text('EMPTY')
            assert_pad('deicing')
            form.locator('[name="pass1_end"]').fill('22:15')
            form.locator('[name="pass2_surface_area"]').select_option('wings_tail')
            form.locator('[name="pass2_start"]').fill('22:16')
            form.locator('[name="pass2_end"]').fill('22:20')
            with coordinator.expect_navigation():
                form.locator('button[type="submit"]').click()
            assert_pad('finished')
            with coordinator.expect_navigation():
                coordinator.locator('button[value="clear"]').click()
            assert_pad('radio-pending')
            for mode,page in zip(('desktop','tv','mobile'),boards):
                page.screenshot(path=str(EVIDENCE/f'{engine}-pad-{mode}.png'))
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
            self.assertLessEqual(tv.evaluate('document.documentElement.scrollHeight-innerHeight'),1)
            with coordinator.expect_navigation():
                coordinator.locator('button[value="radio_complete"]').click()
            for page in boards:
                expect(page.locator('[data-ucc-ramp="Alpha"] [data-ucc-deice-pad] .ucc-pad-outline')).to_be_visible(timeout=10000)
                expect(page.locator('[data-ucc-ramp="Alpha"] .ucc-ramp-diagram [data-ucc-aircraft="'+identity+'"]')).to_have_count(0)
            self.assertFalse(errors,errors)
        finally:
            (EVIDENCE/f'{engine}-measurements.json').write_text(json.dumps(measurements,indent=2))
            if browser:
                browser.close()
            case.tearDownClass()

    def test_chromium_compact_pass_and_pad(self):
        self.exercise('chromium')

    def test_webkit_compact_pass_and_pad(self):
        self.exercise('webkit')
