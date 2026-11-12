"""Truck fleet controls and retained UCC staffing on isolated data."""
import unittest

from playwright.sync_api import expect

from tests.browser import test_neosubzero_ucc as fixtures


class TruckManagementBrowserTest(unittest.TestCase):
    def test_add_edit_service_status_and_archive(self):
        fixture = fixtures.UccBrowserTest()
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        browser = fixture.pw.chromium.launch()
        self.addCleanup(browser.close)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, service_workers="block")
        page = context.new_page()
        fixture.login(page)
        fleet = "/neosubzero/truck-management"
        fixture.ready(page, fleet)
        expect(page.get_by_role("heading", name="TRUCK MANAGEMENT")).to_be_visible()
        page.locator('.subzero-fleet-tools [name="truck_number"]').fill("dz-24")
        with page.expect_navigation():
            page.get_by_role("button", name="ADD TRUCK").click()
        row = page.locator(".subzero-truck-registry li").filter(has_text="DZ-24")
        expect(row).to_contain_text("IN SERVICE")
        row.get_by_label("Edit truck DZ-24").click()
        row.get_by_label("Truck number", exact=True).fill("dz-25")
        with page.expect_navigation():
            row.get_by_role("button", name="SAVE", exact=True).click()
        page.get_by_label("Find truck").fill("25")
        with page.expect_navigation():
            page.get_by_role("button", name="SEARCH").click()
        row = page.locator(".subzero-truck-registry li")
        expect(row).to_have_count(1)
        expect(row).to_contain_text("DZ-25")

        board = context.new_page()
        fixture.ready(board, "/neosubzero/ucc")
        slot = board.locator('[data-ucc-editor="Alpha-1"] [data-ucc-truck-form]')
        team = board.locator('[data-ucc-editor="Alpha-1"]')
        team.locator("[data-ucc-crew-edit]").click()
        with board.expect_response(lambda r: r.url.endswith("/neosubzero/truck") and r.request.method == "POST") as response:
            slot.locator('[name="truck_number"]').select_option("DZ-25")
        self.assertEqual(response.value.status, 200)
        expect(slot.locator('[name="truck_number"]')).to_have_value("DZ-25")
        board.get_by_role("button", name="Close crew editor").click()
        with page.expect_navigation():
            row.get_by_role("button", name="SET OUT OF SERVICE").click()
        expect(row).to_contain_text("OUT OF SERVICE")
        board.bring_to_front()
        retained = team.locator(".ucc-crew-lines strong")
        expect(retained).to_have_css("color", "rgb(255, 156, 156)", timeout=15000)
        expect(retained).to_have_text("1 · TRUCK DZ-25")
        expect(board.locator('[data-ucc-editor="Alpha-1"] [data-current-person-id]:not([data-current-person-id=""])')).to_have_count(1)
        expect(board.locator('[data-ucc-editor="Early Out-1"] option[value="DZ-25"]')).to_have_count(0)

        page.bring_to_front()
        with page.expect_navigation():
            row.get_by_role("button", name="SET IN SERVICE").click()
        expect(row).to_contain_text("IN SERVICE")
        page.once("dialog", lambda dialog: dialog.dismiss())
        row.get_by_role("button", name="DELETE").click()
        expect(row).to_contain_text("DZ-25")
        page.once("dialog", lambda dialog: dialog.accept())
        with page.expect_navigation():
            row.get_by_role("button", name="DELETE").click()
        expect(row).to_contain_text("No trucks match this search")
        board.bring_to_front()
        expect(retained).to_have_css("color", "rgb(255, 156, 156)", timeout=15000)
        expect(retained).to_have_text("1 · TRUCK DZ-25")
