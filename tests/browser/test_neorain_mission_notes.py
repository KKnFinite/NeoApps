"""Legacy note editing in Delay Info and Rainrock's PNG visibility contract."""
from pathlib import Path
import unittest
from unittest.mock import patch

from PIL import Image
from playwright.sync_api import expect

from app.extensions import db
from app.models import Gateway, NeoRainDelayInfo, NeoRainMissionNote, SortDateMission
from app.services.google_rain_integration_mode import set_rain_integration_mode
from tests.browser import test_neorain_compact_overrides as fixture


class NeoRainMissionNotesBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rain = fixture.CompactRainBrowserTest
        cls.rain.setUpClass()
        cls.addClassCleanup(cls.rain.tearDownClass)
        cls.env = cls.rain.fixture
        cls.evidence = Path("instance/browser-evidence/issue90")
        cls.evidence.mkdir(parents=True, exist_ok=True)
        with cls.env.app.app_context():
            gateway = Gateway.query.filter_by(code="RFD").one()
            set_rain_integration_mode(gateway, "night", "neo_only")
            db.session.get(SortDateMission, cls.rain.ids["arrival"]).arrival_status = "cancelled"
            db.session.add_all([
                NeoRainMissionNote(sort_date_mission_id=cls.rain.ids["arrival"], text="Mechanical issue"),
                NeoRainDelayInfo(sort_date_mission_id=cls.rain.ids["departure"], minutes=15, code="WX", notes="Weather delay"),
                NeoRainMissionNote(sort_date_mission_id=cls.rain.ids["departure"], text="Aircraft reassigned to East ramp"),
            ])
            db.session.commit()

    def test_legacy_editor_and_report_export(self):
        for engine, width in (("chromium", 1440), ("webkit", 390)):
            with self.subTest(engine=engine), getattr(self.env.pw, engine).launch() as browser, \
                    patch("app.services.neorain_reports.read_google_recap", side_effect=AssertionError("Unexpected Google read")):
                page = browser.new_page(viewport={"width": width, "height": 880}, accept_downloads=True)
                self.rain().login_for_sort(page)
                self.env().ready(page, "/neorain/inbound")
                arrival_id = self.rain.ids["arrival"]
                if width > 900:
                    page.locator(f'[aria-controls="delay-info-{arrival_id}"]').click()
                    panel = page.locator(f'#delay-info-{arrival_id}')
                else:
                    mobile = page.locator(f'#mobile-flight-{arrival_id}')
                    mobile.locator('[data-neorain-mobile-row-open]').click()
                    panel = mobile.locator('[data-neorain-mobile-delay]')
                    panel.locator('summary').click()
                expect(panel.locator('.neorain-delay-existing-row')).to_have_count(1)
                expect(page.locator('[data-mission-notes]')).to_have_count(0)
                form = panel.locator('.neorain-delay-existing-row .neorain-delay-line-form')
                expect(form.locator('[name="minutes"]')).to_have_value('')
                expect(form.locator('[name="code"]')).to_have_value('')
                form.get_by_role('textbox', name='Notes', exact=True).fill('Mechanical issue; aircraft removed')
                form.get_by_role('button', name='SAVE', exact=True).click()
                expect(panel.locator('[data-neorain-delay-feedback]')).to_have_text('DELAY INFO SAVED.')
                page.screenshot(path=self.evidence / f"legacy-editor-{engine}.png")

                self.env().ready(page, "/neorain/daily-briefing")
                preview = page.locator("#briefing-text")
                expect(preview).to_contain_text("UPS123 — CANCELLED")
                expect(preview).not_to_contain_text("Mechanical issue")
                toggle = page.locator("#briefing-delay-details")
                toggle.check()
                expect(preview).to_contain_text("UPS123 — CANCELLED — Mechanical issue; aircraft removed")
                expect(preview).to_contain_text("15 WX - Weather delay; Aircraft reassigned to East ramp")
                expect(preview).not_to_contain_text("Mission Notes")
                toggle.uncheck()
                expect(preview).not_to_contain_text("Mechanical issue")

                self.env().ready(page, "/neorain/rainrock")
                cancelled = page.locator(f'[data-rainrock-flight="{arrival_id}"]')
                details = page.locator(f'[data-rainrock-flight="{self.rain.ids["departure"]}"] .rainrock-delay')
                self.assertEqual(cancelled.inner_text().strip(), "UPS123 — CANCELLED")
                self.assertEqual(details.inner_text().strip(), "15 WX - Weather delay")
                page.locator("[data-rainrock-notes]").check()
                self.assertEqual(details.inner_text().strip(), "15 WX - Weather delay; Aircraft reassigned to East ramp")
                self.assertEqual(cancelled.inner_text().strip(), "UPS123 — CANCELLED — Mechanical issue; aircraft removed")
                page.evaluate("""() => {
                    const capture = window.html2canvas;
                    window.html2canvas = (report, options) => {
                        window.capturedDetails = report.innerText;
                        return capture(report, options);
                    };
                }""")
                with page.expect_download(timeout=25000) as download:
                    page.locator("[data-rainrock-download]").click()
                file = self.evidence / f"rainrock-{engine}.png"
                download.value.save_as(file)
                with Image.open(file) as image:
                    self.assertGreater(image.width, 900)
                    self.assertGreater(image.height, 100)
                export = page.evaluate("window.capturedDetails")
                self.assertIn("15 WX - Weather delay; Aircraft reassigned to East ramp", export)
                self.assertIn("UPS123 — CANCELLED — Mechanical issue; aircraft removed", export)
