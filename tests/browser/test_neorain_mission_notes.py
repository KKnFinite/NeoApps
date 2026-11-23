"""Signed-in mission notes, guarded live refresh and full cancelled-flight export."""
from datetime import datetime
import unittest
from unittest.mock import patch

from PIL import Image
from playwright.sync_api import expect

from app.extensions import db
from app.models import Gateway, NeoRainMissionNote, SortDateMission
from app.services.google_rain_integration_mode import set_rain_integration_mode
from tests.browser import test_neorain_compact_overrides as fixture


class NeoRainMissionNotesBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rain = fixture.CompactRainBrowserTest
        cls.rain.setUpClass()
        cls.addClassCleanup(cls.rain.tearDownClass)
        cls.env = cls.rain.fixture
        with cls.env.app.app_context():
            gateway = Gateway.query.filter_by(code="RFD").one()
            set_rain_integration_mode(gateway, "night", "neo_only")
            db.session.get(SortDateMission, cls.rain.ids["arrival"]).arrival_status = "cancelled"
            db.session.commit()

    def test_desktop_and_mobile_note_drafts_crud_and_report_export(self):
        for engine, width in (("chromium", 1440), ("webkit", 390)):
            with self.subTest(engine=engine), getattr(self.env.pw, engine).launch() as browser:
                self.exercise(browser, width, engine)

    def test_inbound_note_editor_follows_its_mission_offline(self):
        for engine in ("chromium", "webkit"):
            with self.subTest(engine=engine), getattr(self.env.pw, engine).launch() as browser:
                context = browser.new_context(viewport={"width": 1440, "height": 880}, service_workers="block")
                page = context.new_page()
                self.rain().login_for_sort(page)
                self.env().ready(page, "/neorain/inbound")
                flight = str(self.rain.ids["arrival"])
                desktop = page.locator(f'#delay-info-{flight}')
                mobile = page.locator(f'#mobile-flight-{flight}')
                notes = page.locator(f'[data-mission-notes="{flight}"]')
                expect(notes).to_have_count(1)
                context.set_offline(True)
                page.locator(f'[aria-controls="delay-info-{flight}"]').press("Enter")
                expect(desktop.locator('[data-mission-notes]')).to_be_visible()
                notes.get_by_text("ADD NOTE", exact=True).press("Enter")
                editor = notes.get_by_role("textbox", name="New mission note")
                editor.fill("Local unsaved text")
                page.set_viewport_size({"width": 390, "height": 844})
                opener = mobile.locator("[data-neorain-mobile-row-open]")
                opener.press("Enter")
                expect(mobile.locator('[data-mission-notes]')).to_be_visible()
                expect(editor).to_have_value("Local unsaved text")
                expect(editor.locator("xpath=..").locator('[name="mission_id"]')).to_have_value(flight)
                opener.press("Enter")
                page.set_viewport_size({"width": 1440, "height": 880})
                expect(desktop.locator('[data-mission-notes]')).to_be_visible()
                expect(editor).to_have_value("Local unsaved text")
                expect(notes.locator('[data-mission-note-dirty="true"]')).to_have_count(1)
                context.close()

    def exercise(self, browser, width, engine):
        with self.env.app.app_context():
            NeoRainMissionNote.query.delete()
            db.session.commit()
        page = browser.new_page(viewport={"width": width, "height": 880}, accept_downloads=True)
        self.rain().login_for_sort(page)
        with patch("app.services.neorain_reports.read_google_recap", side_effect=AssertionError("Google must stay off")) as reader:
            for board, direction in (("inbound", "arrival"), ("outbound", "departure")):
                with self.subTest(board=board):
                    self.env().ready(page, "/neorain/" + board)
                    if width > 760:
                        page.locator("[data-neorain-delay-toggle]:visible").first.click()
                    else:
                        page.locator("[data-neorain-mobile-row-open]:visible").first.click()
                    panel = page.locator("[data-mission-notes]:visible")
                    expect(panel).to_have_count(1)
                    panel.get_by_text("ADD NOTE", exact=True).click()
                    draft = panel.get_by_role("textbox", name="New mission note")
                    draft.fill("Mechanical issue" if board == "inbound" else "Diversion coordination")
                    page.evaluate("window.noteDraftPage = true")
                    if board == "inbound":
                        # An upstream revision must arrive while a real draft is dirty.
                        self.rain().upstream(direction, "sta", datetime(2026, 10, 16, 7, 20 if engine == "chromium" else 22))
                        root = page.locator("[data-neorain-inbound-live]")
                        expect(root).to_have_attribute("data-neorain-refresh-deferred", "true", timeout=18000)
                        expect(draft).to_have_value("Mechanical issue")
                        self.assertTrue(page.evaluate("window.noteDraftPage"))
                    panel.get_by_role("button", name="SAVE", exact=True).click()
                    expect(panel.locator("[data-mission-note-status]")).to_have_text("Note saved.")
                    panel.get_by_text("EDIT", exact=True).click()
                    editor = panel.get_by_role("textbox", name="Edit mission note")
                    editor.fill("Mechanical issue; aircraft removed" if board == "inbound" else "Revised coordination")
                    panel.get_by_role("button", name="SAVE", exact=True).click()
                    expect(panel.locator(".neorain-mission-note p")).to_have_text(
                        "Mechanical issue; aircraft removed" if board == "inbound" else "Revised coordination"
                    )
                    page.screenshot(path=str(self.env.evidence / f"notes-{engine}-{board}.png"))
                    if board == "outbound":
                        panel.get_by_text("EDIT", exact=True).click()
                        panel.get_by_role("button", name="DELETE", exact=True).click()
                        expect(panel.locator("[data-mission-note-status]")).to_have_text("Note deleted.")

            self.env().ready(page, "/neorain/daily-briefing")
            preview = page.locator("#briefing-text")
            expect(preview).to_contain_text("UPS123 — CANCELLED")
            expect(preview).not_to_contain_text("Mechanical issue")
            checkbox = page.locator("#briefing-delay-details")
            checkbox.check()
            expect(preview).to_contain_text("UPS123 — CANCELLED — Mechanical issue; aircraft removed")
            checkbox.uncheck()
            expect(preview).not_to_contain_text("Mechanical issue")

            self.env().ready(page, "/neorain/rainrock")
            cancelled = page.locator(f'[data-rainrock-flight="{self.rain.ids["arrival"]}"]')
            expect(cancelled.locator("td")).to_have_count(1)
            self.assertEqual(cancelled.inner_text().strip(), "UPS123 — CANCELLED")
            page.locator("[data-rainrock-notes]").check()
            expect(cancelled).to_have_text("UPS123 — CANCELLED — Mechanical issue; aircraft removed")
            # Capture the real PNG and inspect the exact cloned cells passed to
            # html2canvas, including the absence of hidden operational fields.
            page.evaluate("""() => {
                const capture = window.html2canvas;
                window.html2canvas = (report, options) => {
                    window.capturedCancellation = [...report.querySelectorAll('tr')]
                        .find(row => row.innerText.includes('CANCELLED')).innerText.trim();
                    return capture(report, options);
                };
            }""")
            with page.expect_download(timeout=25000) as download:
                page.locator("[data-rainrock-download]").click()
            file = self.env.evidence / f"cancelled-rainrock-{engine}.png"
            download.value.save_as(file)
            with Image.open(file) as image:
                self.assertGreater(image.width, 900)
                self.assertGreater(image.height, 100)
            self.assertEqual(page.evaluate("window.capturedCancellation"),
                             "UPS123 — CANCELLED — Mechanical issue; aircraft removed")
            reader.assert_not_called()
