"""Shared desktop/mobile icon and physical-position orientation, synthetic only."""
import json
import unittest
from datetime import datetime
from pathlib import Path

from playwright.sync_api import expect
from app.extensions import db
from app.models import SortDateMission, SortDateOperation, SortDateParkingAssignment
from tests.browser import test_neosubzero_ucc as fixtures

EVIDENCE = Path('instance/ducc-orientation-20261105')
SNAPSHOT = '''els=>els.map(e=>({
    parking:e.closest('[data-ucc-parking]')?.dataset.uccParking,
    transform:getComputedStyle(e).transform, mask:getComputedStyle(e).maskImage,
    glyph:e.textContent, color:getComputedStyle(e).backgroundColor,
    cardTransform:getComputedStyle(e.closest('.ucc-plane')).transform,
    labelTransform:getComputedStyle(e.closest('.ucc-plane').querySelector('strong')).transform
}))'''
RIGHT = 'matrix(0, 1, -1, 0, 0, 0)'
LEFT = 'matrix(0, -1, 1, 0, 0, 0)'
UP = 'matrix(1, 0, 0, 1, 0, 0)'


class AircraftOrientationTest(unittest.TestCase):
    def exercise(self, engine):
        case = fixtures.UccBrowserTest()
        case.setUpClass()
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        browser = None
        measurements = []
        try:
            with case.app.app_context():
                operation = db.session.get(SortDateOperation, case.operation_id)
                fixture = fixtures.NeoSubZeroUccTest()
                fixture.operation = operation
                # Fill every physical parking side. These are controlled display
                # fixtures, not fabricated sprays, queue actions or movement.
                for mission in SortDateMission.query.filter_by(sort_date_operation_id=operation.id):
                    mission.actual_block_out_datetime_utc = None
                ids = {}
                for ramp, positions in [('A',range(1,9)),('B',range(1,9)),('C',range(1,9)),('D',range(1,9)),('E',range(1,9)),('R',range(1,5))]:
                    for number in positions:
                        code = f'{ramp}{number:02d}'
                        parking = SortDateParkingAssignment.query.filter_by(sort_date_operation_id=operation.id,position_code=code).one_or_none()
                        if parking:
                            mission = SortDateMission.query.filter_by(sort_date_operation_id=operation.id,assigned_tail_number=parking.tail_number).one()
                        else:
                            mission = fixture._departure('UPS-'+code, 'N-'+code, 'DEN', 3)
                            db.session.add(SortDateParkingAssignment(sort_date_operation_id=operation.id,tail_number=mission.assigned_tail_number,ramp_code=ramp,position_code=code))
                            fixture._deice_event(mission,'deice_planned')
                        ids[code] = mission.id
                db.session.commit()
            browser = getattr(case.pw,engine).launch()
            context = browser.new_context(viewport={'width':1920,'height':1080},service_workers='block')
            page = context.new_page()
            errors = []
            page.on('pageerror',lambda error:errors.append(str(error)))
            case.login(page)
            with page.expect_response(lambda response: response.url.endswith('/images/neosubzero/ducc-aircraft.svg')) as graphic:
                case.ready(page,'/neosubzero/ucc?view=ramp&ramp=Alpha')
            self.assertEqual(graphic.value.status,200)
            for width in (1920,390):
                page.set_viewport_size({'width':width,'height':1080})
                for ramp in ('Alpha','Bravo','Charlie','Delta','Echo','Remote'):
                    if width == 390:
                        page.locator(f'[data-ucc-ramp-select="{ramp}"]').click()
                    icons = page.locator(f'[data-ucc-ramp="{ramp}"] .ucc-ramp-diagram .ucc-plane-icon')
                    expect(icons.first).to_be_visible()
                    snapshots = icons.evaluate_all(SNAPSHOT)
                    # The shared fixture also retains Charlie's real stand 9.
                    self.assertEqual(len(snapshots),4 if ramp == 'Remote' else 9 if ramp == 'Charlie' else 8)
                    for row in snapshots:
                        number = int(row['parking'][-2:])
                        self.assertEqual(row['transform'],UP if ramp == 'Remote' else RIGHT if number <= 4 else LEFT if number <= 8 else UP,row)
                        self.assertIn('/static/images/neosubzero/ducc-aircraft.svg',row['mask'])
                        self.assertEqual(row['glyph'],'')
                        self.assertEqual(row['cardTransform'],'none')
                        self.assertEqual(row['labelTransform'],'none')
                        self.assertNotEqual(row['color'],'rgba(0, 0, 0, 0)')
                    measurements.append(dict(width=width,ramp=ramp,icons=snapshots))
                if width == 390:
                    page.locator('[data-ucc-ramp-select="Alpha"]').click()
                page.screenshot(path=str(EVIDENCE/f'{engine}-{width}-parking.png'),full_page=True)
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
            # The same aircraft change orientation as canonical Block-Out moves
            # them out of side parking into 10 / Middle / 9 via normal refresh.
            with case.app.app_context():
                for number in range(1,4):
                    db.session.get(SortDateMission,ids[f'A{number:02d}']).actual_block_out_datetime_utc = datetime(2026,10,23,5,number)
                db.session.commit()
            pad = page.locator('[data-ucc-ramp="Alpha"] [data-ucc-deice-pad] .ucc-plane')
            # The fixture's configured A03 correctly retains admission priority;
            # this presentation check must not change canonical queue ordering.
            expect(pad).to_have_attribute('data-ucc-aircraft',str(ids['A03']),timeout=15000)
            expect(page.locator('[data-ucc-ramp="Alpha"] [data-ucc-queue-position="1"] .ucc-plane')).to_have_attribute('data-ucc-aircraft',str(ids['A01']))
            expect(page.locator('[data-ucc-ramp="Alpha"] [data-ucc-queue-position="2"] .ucc-plane')).to_have_attribute('data-ucc-aircraft',str(ids['A02']))
            for width in (390,1920):
                page.set_viewport_size({'width':width,'height':1080})
                for location in ('[data-ucc-deice-pad]','[data-ucc-queue-position="1"]','[data-ucc-queue-position="2"]'):
                    icon = page.locator('[data-ucc-ramp="Alpha"] '+location+' .ucc-plane-icon')
                    expect(icon).to_have_css('transform',UP)
                page.screenshot(path=str(EVIDENCE/f'{engine}-{width}-throat.png'),full_page=True)
            self.assertFalse(errors,errors)
        finally:
            (EVIDENCE/f'{engine}-measurements.json').write_text(json.dumps(measurements,indent=2))
            if browser:
                browser.close()
            case.tearDownClass()

    def test_chromium_shared_orientation(self):
        self.exercise('chromium')

    def test_webkit_shared_orientation(self):
        self.exercise('webkit')
