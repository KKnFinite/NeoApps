"""Exercise Coordinator correction through the actual Dispatch form at each stage."""
import unittest
from datetime import datetime

from playwright.sync_api import expect

from app.extensions import db
from app.models import SortDateMission, SortDateOperation, User
from app.services.neosubzero_departure_deice import mutate_departure_deice
from tests.browser import test_neosubzero_operations as fixtures
from tests.browser import test_neosubzero_ucc as ucc


class DispatchResetBrowserTest(unittest.TestCase):
    def exercise(self, engine, width):
        case = fixtures.OperationsBrowserTest()
        case.setUpClass()
        self.addCleanup(case.tearDownClass)
        browser = getattr(case.pw, engine).launch()
        self.addCleanup(browser.close)
        context = browser.new_context(viewport={'width': width, 'height': 844})
        page = context.new_page()
        case.login(page)
        reasons = []
        def confirm(dialog):
            if dialog.type == 'prompt':
                reasons.append(dialog.message)
                dialog.accept('Dispatch training correction')
            else:
                dialog.accept()
        page.on('dialog', confirm)
        posts = []
        page.on('request', lambda req: posts.append(req.url) if req.method == 'POST' else None)
        path = f'/neosubzero/coordinator?ramp=Delta&mission={case.flow_id}'
        row = page.locator(f'[data-ops-row][data-mission-id="{case.flow_id}"]')
        for stage in ('planned', 'configured', 'partial', 'start', 'fin', 'clear', 'radio', 'gone', 'departed'):
            with self.subTest(stage=stage):
                with case.app.app_context():
                    operation = db.session.get(SortDateOperation, case.operation_id)
                    mission = db.session.get(SortDateMission, case.flow_id)
                    actor = User.query.filter_by(username='drawer-admin').one()
                    def mutate(action, **values):
                        return mutate_departure_deice(operation, mission, action, values, actor_id=actor.id)
                    mutate('initial_contact')
                    if stage == 'configured':
                        mutate('toggle_configured')
                    if stage in ('partial', 'start', 'fin', 'clear', 'radio'):
                        mutate('save_treatment', treatment_plan='one_type_i', pass1_surface_area='wings_only',
                               pass1_start='' if stage == 'partial' else '22:10',
                               pass1_end='22:15' if stage in ('fin','clear','radio') else '')
                    if stage in ('gone', 'departed'):
                        mutate('manual_not_sprayed')
                    if stage in ('clear','radio','gone','departed'):
                        mutate('clear')
                    if stage == 'departed':
                        mission.actual_block_out_datetime_utc = datetime(2026,10,23,3,30)
                        mission.actual_block_out_source = 'google_motherbrain'
                        mission.departure_status = 'departed'
                    if stage == 'radio':
                        mutate('radio_complete')
                    db.session.commit()
                case.ready(page, path)
                control = row.locator('button[value="reset"]:visible')
                expect(control).to_be_visible()
                if stage == 'radio':
                    ducc = context.new_page()
                    ducc.set_viewport_size({'width': 1920, 'height': 1080})
                    ucc.UccBrowserTest.ready(case, ducc, '/neosubzero/ucc')
                    plane = ducc.locator(f'[data-ucc-ramp="Delta"] [data-ucc-aircraft="{case.flow_id}"]')
                    expect(plane).to_have_count(0)
                if stage == 'planned':
                    note = row.get_by_role('textbox', name='Departure notes', include_hidden=True)
                    # Expand the native disclosures using the same summary controls as an operator.
                    detail = row.locator('[data-ops-disclosure="mission-details"]')
                    if detail.get_attribute('open') is None and detail.locator(':scope > summary').is_visible():
                        detail.locator(':scope > summary').click()
                    row.locator('.ops-inline-notes > summary').click()
                    note.fill('unsaved training draft')
                    count = len(posts)
                    row.get_by_role('button', name='Discard unsaved edits').click()
                    expect(row.locator('[data-ops-discard-draft]')).to_be_hidden()
                    self.assertEqual(posts[count:], [])
                    case.ready(page, path)
                    expect(row.locator('textarea[name="notes"]')).to_have_value('')
                    page.wait_for_load_state('networkidle')
                count = len(posts)
                case.submit(page, control)
                self.assertEqual(len(posts), count + 1)
                expected = 'gone' if stage == 'departed' else 'untreated'
                expect(row).to_have_attribute('data-status',expected)
                if stage == 'departed':
                    expect(row).to_contain_text('GOOGLE MOTHERBRAIN')
                    expect(row.get_by_text('MotherBrain mission detail')).to_have_count(1)
                    expect(row.locator('button[value="reset"]')).to_have_count(0)
                else:
                    expect(row.locator('button[value="initial_contact"]:visible')).to_be_visible()
                case.ready(page, path)
                expect(row).to_have_attribute('data-status',expected)
                if stage == 'radio':
                    ducc.reload()
                    expect(plane).to_have_class('ucc-plane state-normal')
                    expect(ducc.locator(f'[data-ucc-deice-pad] [data-ucc-aircraft="{case.flow_id}"]')).to_have_count(0)
                    ducc.evaluate('document.documentElement.requestFullscreen = undefined')
                    ducc.locator('[data-ucc-tv-enter]').click()
                    tv_plane = ducc.locator('.ucc-tv-wallboard .ucc-tv-stands .ucc-tv-plane[aria-label*="N777UP"]')
                    expect(tv_plane).to_be_visible()
                    ducc.close()
        self.assertEqual(len(reasons), 9)

    def test_chromium_desktop(self):
        self.exercise('chromium', 1366)

    def test_webkit_phone(self):
        self.exercise('webkit', 390)
