"""Both reports share one editor and one Sort in the signed-in workspace."""
import unittest
from pathlib import Path

from playwright.sync_api import expect

from app.extensions import db
from app.models import NeoRainReportEntry
from tests.browser import test_neorain_compact_overrides as fixtures


class ReportsWorkspaceBrowserTest(unittest.TestCase):
    def test_shared_editor_tabs_conflicts_and_exports(self):
        host = fixtures.CompactRainBrowserTest
        host.setUpClass()
        self.addCleanup(host.tearDownClass)
        evidence = Path("instance/browser-evidence/issue74")
        evidence.mkdir(parents=True, exist_ok=True)
        for engine, width in (("chromium", 1440), ("webkit", 390)):
            with self.subTest(engine=engine), getattr(host.fixture.pw, engine).launch() as browser:
                page = browser.new_page(viewport={"width": width, "height": 900},
                                        accept_downloads=True, service_workers="block")
                host().login_for_sort(page)
                host.fixture().ready(page, f"/neorain/reports?operation_id={host.operation_id}")
                page.evaluate("window.originalReports = document.querySelector('[data-neorain-reports]')")
                editor = page.locator("[data-sort-inputs]")
                expect(editor).to_have_js_property("open", False)
                editor.locator("summary").click()
                fields = page.locator("[data-report-field]")
                names = fields.evaluate_all("fields => fields.map(field => field.name)")
                self.assertEqual(len(names), 20)
                self.assertEqual(len(names), len(set(names)))
                volume = page.locator("[data-report-field][name=actual_volume]")
                value = "12345" if engine == "chromium" else "12346"
                volume.fill(value)
                briefing = page.get_by_role("tab", name="Daily Briefing")
                with page.expect_response(lambda r: "/reports/inputs" in r.url) as response:
                    briefing.click()
                self.assertEqual(response.value.status, 200)
                expect(page.locator("#briefing-text")).to_contain_text(f"Volume Actual: {int(value):,}")
                expect(page.locator("[data-rainrock-value=actual_volume]")).to_have_text(f"{int(value):,}")
                expect(page.locator("[name=operation_id]")).to_have_value(str(host.operation_id))
                self.assertTrue(page.evaluate("originalReports === document.querySelector('[data-neorain-reports]')"))
                page.locator("#briefing-delay-details").check()
                expect(page.locator("[data-briefing-delay-mode]")).to_have_text("ON")
                page.evaluate("navigator.clipboard.writeText = async text => { window.reportCopy = text; }")
                page.locator("#copy-briefing").click()
                expect(page.locator("#copy-briefing-status")).to_have_text("Copied.")
                self.assertEqual(page.evaluate("reportCopy"), page.locator("#briefing-text").text_content())

                with host.fixture.app.app_context():
                    remote = NeoRainReportEntry.query.filter_by(sort_date_operation_id=host.operation_id,
                        field_name="actual_volume", data_source="manual").one()
                    remote.value = "20000"
                    remote.version += 1
                    db.session.commit()
                volume.fill("18100")
                rainrock = page.get_by_role("tab", name="Rainrock")
                with page.expect_response(lambda r: "/reports/inputs" in r.url) as conflict:
                    rainrock.click()
                self.assertEqual(conflict.value.status, 409)
                expect(volume).to_have_value("18100")
                expect(page.locator("[data-field-status=actual_volume]")).to_contain_text("changed")
                page.locator("[data-report-use-saved=actual_volume]").click()
                expect(volume).to_have_value("20000")
                expect(page.locator("[data-rainrock-value=actual_volume]")).to_have_text("20,000")
                expect(page.locator("#briefing-text")).to_contain_text("Volume Actual: 20,000")
                editor.locator("summary").click()
                with page.expect_download() as receipt:
                    page.locator("[data-rainrock-download]").click()
                with open(receipt.value.path(), "rb") as image:
                    self.assertEqual(image.read(8), b"\x89PNG\r\n\x1a\n")
                page.screenshot(path=evidence / f"rainrock-{engine}.png")
                width_info = page.evaluate("""() => ({
                    viewport: innerWidth,
                    document: document.documentElement.scrollWidth,
                    overflow: [...document.querySelectorAll('[data-neorain-reports] *')].filter(el =>
                        !el.closest('.rainrock-table-scroll') && el.getBoundingClientRect().right > innerWidth + 1
                    ).slice(0, 10).map(el => [el.tagName, el.className, el.getBoundingClientRect().right]),
                })""")
                self.assertLessEqual(width_info["document"], width_info["viewport"] + 1, width_info)
                rainrock.press("End")
                expect(briefing).to_have_attribute("aria-selected", "true")
                expect(page.locator("[data-briefing-delay-mode]")).to_have_text("ON")
                page.screenshot(path=evidence / f"briefing-{engine}.png")
                host.fixture().ready(page, f"/neorain/reports?operation_id={host.operation_id}")
                expect(briefing).to_have_attribute("aria-selected", "true")
                host.fixture().ready(page, f"/neorain/rainrock?operation_id={host.operation_id}")
                expect(rainrock).to_have_attribute("aria-selected", "true")
                host.fixture().ready(page, f"/neorain/daily-briefing?operation_id={host.operation_id}")
                expect(briefing).to_have_attribute("aria-selected", "true")
