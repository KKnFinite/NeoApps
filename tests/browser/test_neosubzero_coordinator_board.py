"""Focused real Coordinator palette and shared DUCC Board View regressions."""
import json
import re
import unittest
from datetime import datetime
from pathlib import Path

from playwright.sync_api import expect
from app.extensions import db
from app.models import SortDateMission, SortDateOperation, SortDateParkingAssignment, User
from app.services.neosubzero_evidence import evidence_values
from tests.browser import test_neosubzero_ucc as fixtures
from tests.browser import test_neosubzero_palette as palette

EVIDENCE = Path('instance/coordinator-ducc-20261105')
COLORS = {key: value[1] for key, value in palette.PALETTE.items() if key != 'no-deice'}


class CoordinatorBoardTest(unittest.TestCase):
    def exercise(self, engine):
        case = fixtures.UccBrowserTest()
        case.setUpClass()
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        browser = None
        measurements = []
        try:
            with case.app.app_context():
                from app.models import NeoSubZeroDepartureDeiceEvent
                operation = db.session.get(SortDateOperation, case.operation_id)
                fixture = fixtures.NeoSubZeroUccTest()
                fixture.operation = operation
                actor = User.query.filter_by(username='drawer-admin').one()
                ids = {}
                for i, state in enumerate(COLORS):
                    mission = db.session.get(SortDateMission, case.missions[i]) if i < len(case.missions) else fixture._departure('UPS-UI7', 'N-UI7', 'DEN', 4)
                    mission.actual_block_out_datetime_utc = None
                    assignment = SortDateParkingAssignment.query.filter_by(sort_date_operation_id=operation.id, tail_number=mission.assigned_tail_number).one_or_none()
                    if assignment is None:
                        assignment = SortDateParkingAssignment(sort_date_operation_id=operation.id, tail_number=mission.assigned_tail_number)
                        db.session.add(assignment)
                    assignment.ramp_code, assignment.position_code = 'A', f'A0{i+1}'
                    event = NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=mission.id).one_or_none() or fixture._deice_event(mission, 'deice_planned')
                    event.status = {'planned':'deice_planned', 'configured':'configured', 'deicing':'configured', 'fin':'finished', 'radio-pending':'cleared', 'negative':'negative', 'gone':'cleared'}[state]
                    event.treatment_plan = 'one_type_i' if state != 'negative' else None
                    event.pass1_surface_area = 'wings_only' if state != 'negative' else None
                    event.configured_at_utc = datetime(2026,10,23,4) if state not in ('planned','negative') else None
                    event.pass1_started_at_utc = datetime(2026,10,23,4,1) if state in ('deicing','fin','radio-pending','gone') else None
                    event.pass1_ended_at_utc = datetime(2026,10,23,4,5) if state in ('fin','radio-pending','gone') else None
                    event.pass_evidence = {'1':{'fluid':'Type I','concentration':50}} if event.pass1_started_at_utc else None
                    event.cleared_at_utc = datetime(2026,10,23,4,6) if event.status == 'cleared' else None
                    event.radio_state = 'pending' if state == 'radio-pending' else 'complete' if state == 'gone' else None
                    event.radio_completed_at_utc = datetime(2026,10,23,4,7) if state == 'gone' else None
                    event.radio_completed_by_user_id = actor.id if state == 'gone' else None
                    ids[state] = mission.id
                db.session.commit()
                original = [evidence_values(e) for e in NeoSubZeroDepartureDeiceEvent.query.order_by(NeoSubZeroDepartureDeiceEvent.id)]
            browser = getattr(case.pw, engine).launch()
            context = browser.new_context(viewport={'width':1920,'height':1080}, service_workers='block')
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            case.login(page)
            case.ready(page, '/neosubzero/coordinator?ramp=Alpha')
            for width in (390,1920):
                page.set_viewport_size({'width':width,'height':1080})
                for state, color in COLORS.items():
                    card = page.locator(f'.coordinator-aircraft[href*="mission={ids[state]}"]')
                    expect(card).to_have_class(re.compile('state-' + state))
                    expect(card).to_have_css('background-color', palette.rgb(color))
                    foreground = card.evaluate('e=>getComputedStyle(e).color')
                    self.assertGreaterEqual(palette.contrast(palette.rgb(color), foreground), 4.5)
                page.screenshot(path=str(EVIDENCE/f'{engine}-coordinator-{width}.png'), full_page=True)
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'), 1)
            case.ready(page, '/neosubzero/ucc')
            expect(page).to_have_title('DUCC | NeoSub-Zero')
            expect(page.locator('.neosubzero-head h1')).to_have_text('DUCC')
            for width in (1280,1440,1920,2560):
                page.set_viewport_size({'width':width,'height':1080})
                self.check_desktop_layout(page)
                if width in (1440,1920):
                    page.screenshot(path=str(EVIDENCE/f'{engine}-desktop-{width}.png'),full_page=True)
            for state, color in COLORS.items():
                plane = page.locator(f'[data-ucc-aircraft="{ids[state]}"]')
                expect(plane).to_have_css('background-color','rgb(9, 23, 33)')
                expect(plane.locator('.ucc-plane-icon')).to_have_css('color',palette.rgb(color))
                expect(plane.locator('.ucc-plane-status')).to_be_hidden()
                expect(plane.locator('.ucc-plane-etd')).to_be_visible()
            page.locator('[data-operational-sidebar] [data-operational-board-toggle]').click()
            expect(page.locator('body')).to_have_class(re.compile('operational-board-view'))
            expect(page.locator('.operational-board-exit')).to_have_text('GO BACK')
            for width, height in ((1920,1080),(1440,900),(1024,768),(2560,1440)):
                page.set_viewport_size({'width':width,'height':height})
                expect(page.locator('[data-operational-topbar]')).to_be_hidden()
                expect(page.locator('[data-operational-sidebar]')).to_be_hidden()
                expect(page.locator('[data-ucc-ramp]:visible')).to_have_count(6)
                self.assertEqual(page.locator('[data-ucc-ramp]:visible').evaluate_all('els=>els.map(e=>e.dataset.uccRamp)'), ['Alpha','Bravo','Charlie','Delta','Echo','Remote'])
                # Wait for the event-driven fit, never a fixed timing delay.
                page.wait_for_function('''() => {
                    const r=document.querySelector('[data-subzero-ucc]').getBoundingClientRect();
                    return r.right<=innerWidth && r.bottom<=innerHeight && r.width>=innerWidth*.96;
                }''')
                bounds = page.locator('[data-subzero-ucc]').bounding_box()
                self.assertLessEqual(page.evaluate('document.documentElement.scrollHeight-innerHeight'), 1)
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'), 1)
                measurements.append({'width':width,'height':height,'bounds':bounds})
                page.screenshot(path=str(EVIDENCE/f'{engine}-board-{width}.png'))
            # Open editors and retain a real unsaved draft while live refresh
            # replaces physical diagrams; the fitter must measure the new board.
            editor = page.locator('[data-ucc-editor="Alpha-1"]')
            editor.locator(':scope > summary').click()
            draft = editor.locator('[name="truck_number"]')
            draft.fill('UNSAVED BOARD DRAFT')
            with case.app.app_context():
                mission = db.session.get(SortDateMission, ids['planned'])
                mission.flight_number = 'UPS-LIVE-UI'
                db.session.commit()
            expect(page.locator('[data-ucc-aircraft]').filter(has_text='UPS-LIVE-UI')).to_be_visible(timeout=15000)
            expect(draft).to_have_value('UNSAVED BOARD DRAFT')
            self.assertTrue(page.locator('[data-subzero-ucc]').evaluate('e=>e.getBoundingClientRect().bottom<=innerHeight'))
            page.locator('.operational-board-exit').click()
            expect(page.locator('[data-operational-topbar]')).to_be_visible()
            expect(draft).to_have_value('UNSAVED BOARD DRAFT')
            # Saved Board preference is desktop-only across breakpoint changes.
            page.locator('[data-operational-sidebar] [data-operational-board-toggle]').click()
            page.set_viewport_size({'width':390,'height':844})
            expect(page.locator('body')).not_to_have_class(re.compile('operational-board-view'))
            page.locator('[data-ucc-view="ramp"]').click()
            expect(page.locator('[data-ucc-ramp]:visible')).to_have_count(1)
            expect(page.locator('.neosubzero-weather')).to_be_hidden()
            for state,color in COLORS.items():
                expect(page.locator(f'[data-ucc-aircraft="{ids[state]}"]')).to_have_css('background-color',palette.rgb(color))
            page.set_viewport_size({'width':1920,'height':1080})
            expect(page.locator('body')).to_have_class(re.compile('operational-board-view'))
            expect(page.locator('[data-ucc-ramp]:visible')).to_have_count(6)
            page.locator('.operational-board-exit').click()
            # TV baseline is the wall display, with this still-unsaved editor
            # collapsed. Collapsing never discards its input or changes work.
            editor.locator(':scope > summary').click()
            expect(draft).to_have_value('UNSAVED BOARD DRAFT')
            page.locator('[data-ucc-tv-enter]').click()
            expect(page.locator('body')).to_have_class(re.compile('neosubzero-ucc-tv-mode'))
            page.wait_for_function('''() => document.querySelector('[data-subzero-ucc]').getBoundingClientRect().bottom<=innerHeight''')
            self.check_desktop_layout(page)
            self.assertLessEqual(page.evaluate('document.documentElement.scrollHeight-innerHeight'),1)
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
            fonts=page.evaluate('''() => {
                const root=document.querySelector('[data-subzero-ucc]'),scale=root.getBoundingClientRect().width/root.offsetWidth;
                return {tails:Math.min(...[...root.querySelectorAll('.ucc-plane strong')].map(e=>parseFloat(getComputedStyle(e).fontSize)*scale)),names:Math.min(...[...root.querySelectorAll('.ucc-team summary small')].map(e=>parseFloat(getComputedStyle(e).fontSize)*scale))};
            }''')
            self.assertGreaterEqual(fonts['tails'],12)
            self.assertGreaterEqual(fonts['names'],11)
            page.screenshot(path=str(EVIDENCE/f'{engine}-tv-desktop.png'))
            page.locator('[data-ucc-tv-exit]').click()
            expect(page.locator('[data-operational-topbar]')).to_be_visible()
            with case.app.app_context():
                self.assertEqual(original, [evidence_values(e) for e in NeoSubZeroDepartureDeiceEvent.query.order_by(NeoSubZeroDepartureDeiceEvent.id)])
            self.assertFalse(errors, errors)
        finally:
            (EVIDENCE/f'{engine}-measurements.json').write_text(json.dumps(measurements, indent=2))
            if browser:
                browser.close()
            case.tearDownClass()

    def check_desktop_layout(self, page):
        # Equal physical dimensions, four staffing summaries above each diagram,
        # and a single real weather strip rather than duplicated mobile markup.
        page.wait_for_function('''() => {
            const ramps=[...document.querySelectorAll('[data-ucc-ramp]')];
            return ramps.every(r=>r.querySelector('.ucc-staffing-grid').getBoundingClientRect().bottom<=r.querySelector('.ucc-ramp-diagram').getBoundingClientRect().top+1);
        }''')
        geometry = page.evaluate('''() => {
            const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom};};
            return {ramps:[...document.querySelectorAll('[data-ucc-ramp]')].slice(0,5).map(r=>({ramp:rect(r),diagram:rect(r.querySelector('.ucc-ramp-diagram')),teams:r.querySelectorAll('.ucc-team').length})),
                weather:rect(document.querySelector('.neosubzero-weather')),upcoming:rect(document.querySelector('[data-ucc-upcoming]')),
                remote:rect(document.querySelector('[data-ucc-ramp="Remote"]')),
                overflow:document.documentElement.scrollWidth-innerWidth};
        }''')
        self.assertLessEqual(geometry['overflow'],1)
        self.assertLessEqual(abs(geometry['weather']['y']-geometry['upcoming']['y']),1)
        for key in ('width','height','y'):
            values=[r['diagram'][key] for r in geometry['ramps']]
            self.assertLessEqual(max(values)-min(values),1,(key,geometry))
        self.assertTrue(all(r['teams']==4 for r in geometry['ramps']))
        self.assertGreaterEqual(geometry['remote']['y'],max(r['ramp']['bottom'] for r in geometry['ramps']))

    def test_chromium_coordinator_and_board(self):
        self.exercise('chromium')

    def test_webkit_coordinator_and_board(self):
        self.exercise('webkit')
