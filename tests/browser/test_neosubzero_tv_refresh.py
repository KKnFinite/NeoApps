"""Cross-session DUCC TV refresh through the real staffing and settings routes."""
import json
import unittest
from urllib.parse import parse_qs, urlsplit

from flask import request as flask_request
from playwright.sync_api import expect

from app import create_app
from app.extensions import db
from app.models import Gateway, SortDateOperation, StaffingDailyAttendance, StaffingUnit
from app.services.live_screen_refresh import live_screen_refresh_value, save_live_screen_refresh_override
from tests.browser import test_neosubzero_ucc as ucc_fixture


class DuccTvRefreshBrowserTest(unittest.TestCase):
    def exercise(self, engine):
        case = ucc_fixture.UccBrowserTest()
        case.setUpClass()
        self.addCleanup(case.tearDownClass)
        case.refresh.stop()
        case.app.config["NEOSUBZERO_UNLOAD_LOAD_ATTENDANCE_ENABLED"] = True
        failures = set()

        @case.app.before_request
        def simulate_unavailable_refresh():
            if flask_request.headers.get("X-Test-DUCC-Display") != "primary":
                return None
            if (("fragment" in failures and flask_request.path == "/neosubzero/ucc"
                 and flask_request.args.get("fragment") == "1")
                    or ("revision" in failures and flask_request.path == "/neosubzero/ucc/revision")):
                return {"ok": False}, 503
            return None
        with case.app.app_context():
            gateway = Gateway.query.filter_by(code="RFD").one()
            save_live_screen_refresh_override(gateway, "neosubzero.ucc", "off")
            db.session.commit()

        browser = getattr(case.pw, engine).launch()
        self.addCleanup(browser.close)
        work = browser.new_page(viewport={"width": 1920, "height": 1080})
        case.login(work)
        case.ready(work, "/neosubzero/ucc")
        expect(work.locator("[data-subzero-ucc]")).to_have_attribute("data-refresh-interval-ms", "0")

        def is_revision(request):
            return urlsplit(request.url).path.endswith("/ucc/revision")

        def is_fragment(request):
            url = urlsplit(request.url)
            return url.path.endswith("/ucc") and parse_qs(url.query).get("fragment") == ["1"]

        def open_tv(primary=False):
            page = browser.new_page(viewport={"width": 1920, "height": 1080})
            if primary:
                page.set_extra_http_headers({"X-Test-DUCC-Display": "primary"})
            case.login(page)
            page.clock.install()
            page.add_init_script("localStorage.setItem('neoapps.neosubzero.ucc.tv-mode','1')")
            requests = []
            page.on("requestfinished", lambda r: requests.append(r) if is_revision(r) or is_fragment(r) else None)
            with page.expect_response(lambda r: is_revision(r.request)) as first:
                case.ready(page, "/neosubzero/ucc")
            expect(page.locator(".ucc-tv-wallboard")).to_be_visible()
            self.assertIn("view=tv", first.value.url)
            return page, requests, first.value.json()["refresh"]["live_screen_refresh_interval_ms"]

        tv, requests, default = open_tv(primary=True)
        self.assertEqual(default, 30000)
        navigations = []
        tv.on("framenavigated", lambda frame: navigations.append(frame.url) if frame == tv.main_frame else None)
        board = tv.locator(".ucc-tv-wallboard")
        board.get_by_role("button", name="Light appearance").click()
        expect(board).to_have_attribute("data-ucc-tv-theme", "light")

        settings = browser.new_page()
        case.login(settings)
        settings_path = "/motherbrain/system-settings/node-refresh-timings"
        case.ready(settings, settings_path)
        setting = settings.locator('[data-refresh-screen="neosubzero.ucc_tv"]')
        expect(setting).to_contain_text("30 seconds")
        setting.locator("select").select_option("5")
        setting.get_by_role("button", name="SAVE").click()
        expect(setting.locator("select")).to_have_value("5")

        def tick(page, milliseconds):
            with page.expect_response(lambda r: is_revision(r.request)) as refreshed:
                page.clock.fast_forward(milliseconds)
            payload = refreshed.value.json()
            page.wait_for_timeout(250)
            return payload

        self.assertEqual(tick(tv, 30000)["refresh"]["live_screen_refresh_interval_ms"], 5000)
        second, _, current = open_tv()
        self.assertEqual(current, 5000)
        self.assertFalse(any(is_fragment(request) for request in requests))
        self.assertEqual(len(requests), 2)
        unchanged_bytes = sum(request.sizes()["responseBodySize"] for request in requests)

        slot = work.locator('[data-ucc-editor="Alpha-2"]')

        def assign(role, value, team=None):
            team = team if team is not None else slot
            form = team.locator(f'[data-ucc-crew-form="{role}"]')
            pencil = team.locator(f'[data-ucc-crew-edit="{role}"]')
            select = form.locator("select")
            version = form.locator('[name="expected_version"]')
            previous = version.input_value()
            if not select.is_visible():
                expect(pencil).to_be_visible()
                pencil.click()
                expect(team.locator(".ucc-team-controls:popover-open")).to_be_visible()
                if role == "truck":
                    position = team.get_attribute("data-ucc-editor").rsplit("-", 1)[1]
                    expect(select.locator("option:checked")).to_have_text(f"{position}. {select.input_value()}")
            expect(select).to_be_visible()
            replacement = role != "truck" and bool(value) and bool(select.input_value())
            with work.expect_response(lambda r: r.request.method == "POST", timeout=7000) as result:
                select.select_option(str(value))
                if replacement:
                    work.locator('[data-ucc-move-choice="remove"]').click()
            self.assertEqual(result.value.status, 200, (role, value, result.value.text()))
            expect(team.locator("[data-ucc-management-save]")).to_have_text("Saved")
            expect(version).not_to_have_value(previous)
            if value:
                if team.locator(".ucc-team-controls:popover-open").is_visible():
                    team.locator("[data-ucc-crew-close]").click()
                expect(pencil).to_be_visible()
                expect(select).to_be_hidden()
                if role == "truck":
                    position = team.get_attribute("data-ucc-editor").rsplit("-", 1)[1]
                    expect(team.locator('[data-ucc-crew-line="truck"] span')).to_have_text(f"{position}. {value}")
                    expect(select).to_have_value(value)
            else:
                expect(team.locator(".ucc-team-controls:popover-open")).to_have_count(0)
                expect(pencil).to_be_hidden()
                expect(select).to_be_visible()

        alpha = board.locator(".ucc-tv-ramp").filter(has=tv.get_by_role("heading", name="Alpha", exact=True))
        tv_alpha_slot = alpha.locator('[data-ucc-tv-position="2"]')
        expect(tv_alpha_slot).to_have_count(0)
        expect(board.locator(".ucc-tv-crew").first).not_to_contain_text("Unassigned")
        geometry = alpha.locator(".ucc-tv-stands").bounding_box()
        second.set_viewport_size({"width": 1366, "height": 768})
        expect(second.locator(".ucc-tv-wallboard")).to_have_attribute("data-ucc-tv-theme", "dark")
        for role, initial, replacement, label, replacement_label in (
            ("driver", case.people[1], case.people[2], "D · T. Longname-Deicer-1", "D · T. Longname-Deicer-2"),
            ("flyer", case.people[1], case.people[2], "F · T. Longname-Deicer-1", "F · T. Longname-Deicer-2"),
            ("truck", "12", "UCC-831", "2. 12", "2. UCC-831"),
        ):
            for value, visible in ((initial, label), (replacement, replacement_label), ("", None)):
                assign(role, value)
                self.assertTrue(tick(tv, 5000)["changed"])
                if visible:
                    expect(alpha).to_contain_text(visible)
                    expect(tv_alpha_slot.locator("[data-ucc-tv-crew-field]")).to_have_count(1)
                else:
                    expect(tv_alpha_slot).to_have_count(0)
                self.assertEqual(alpha.locator(".ucc-tv-stands").bounding_box(), geometry)
                expect(board).to_have_attribute("data-ucc-tv-theme", "light")
                if role == "driver" and value == initial:
                    self.assertTrue(tick(second, 5000)["changed"])
                    expect(second.locator(".ucc-tv-wallboard")).to_contain_text(label)

        work.set_viewport_size({"width": 1366, "height": 768})
        tv.set_viewport_size({"width": 1366, "height": 768})
        for section in ("Bravo", "Charlie", "Delta", "Echo", "Remote", "Early Out"):
            team = work.locator(f'[data-ucc-editor="{section}-4"]')
            expect(team.locator('[data-ucc-auto-save] select:visible')).to_have_count(3)
            expect(team.locator('[data-ucc-crew-edit]:visible')).to_have_count(0)
            if section in ("Remote", "Early Out"):
                target = board.locator(".ucc-tv-remote" if section == "Remote" else ".ucc-tv-early")
            else:
                target = board.locator(".ucc-tv-ramp").filter(has=tv.get_by_role("heading", name=section, exact=True))
            tv_slot = target.locator('[data-ucc-tv-position="4"]')
            geometry = alpha.locator(".ucc-tv-stands").bounding_box()
            for role, value in (("truck", "UCC-831"), ("driver", case.people[1]), ("flyer", case.people[2])):
                assign(role, value, team)
                tick(tv, 5000)
                resource = tv_slot.locator(f'[data-ucc-tv-crew-field="{role}"]')
                expect(resource).to_be_visible()
                if role == "truck":
                    expect(resource).to_have_text(f"4. {value}")
                expect(tv_slot.locator("[data-ucc-tv-crew-field]")).to_have_count(1)
                assign(role, "", team)
                tick(tv, 5000)
                expect(tv_slot).to_have_count(0)
                self.assertEqual(alpha.locator(".ucc-tv-stands").bounding_box(), geometry)
        self.assertEqual(work.evaluate("document.documentElement.scrollWidth - innerWidth"), 0)
        self.assertLessEqual(board.evaluate("el => el.scrollWidth"), 1366)

        assign("driver", case.people[1])
        assign("flyer", case.people[2])
        assign("truck", "12")
        assign("truck", "")
        tick(tv, 5000)
        expect(tv_alpha_slot.locator("[data-ucc-tv-crew-field]")).to_have_count(2)
        expect(tv_alpha_slot.locator('[data-ucc-tv-crew-field="truck"]')).to_have_count(0)
        assign("truck", "12")
        work.keyboard.press("Escape")
        case.ready(work, "/neosubzero/ucc")
        slot.locator('[data-ucc-crew-edit="driver"]').click()
        unit = slot.locator("[data-ucc-unit-form]")
        slot.locator('[data-ucc-editor="unit-Alpha-2"] > summary').click()
        unit.locator('[name="destination_ramp"]').select_option("Bravo")
        unit.locator('[name="destination_position"]').select_option("2")
        unit.locator('button[type="submit"]').click()
        expect(slot.locator("[data-ucc-management-save]")).to_have_text("Saved")
        tick(tv, 5000)
        bravo = board.locator(".ucc-tv-ramp").filter(has=tv.get_by_role("heading", name="Bravo", exact=True))
        expect(bravo).to_contain_text("2. 12")
        expect(bravo).to_contain_text("D · T. Longname-Deicer-1")
        expect(bravo).to_contain_text("F · T. Longname-Deicer-2")

        # Reassign an occupied team via the audited whole-crew swap.
        case.ready(work, "/neosubzero/ucc")
        bravo_slot = work.locator('[data-ucc-editor="Bravo-2"]')
        bravo_slot.locator('[data-ucc-crew-edit="driver"]').click()
        bravo_slot.locator('[data-ucc-editor="unit-Bravo-2"] > summary').click()
        unit = bravo_slot.locator("[data-ucc-unit-form]")
        unit.locator('[name="destination_ramp"]').select_option("Alpha")
        unit.locator('[name="destination_position"]').select_option("1")
        unit.locator('[name="resolution"]').select_option("swap")
        unit.locator('button[type="submit"]').click()
        expect(bravo_slot.locator("[data-ucc-management-save]")).to_have_text("Saved")
        tick(tv, 5000)
        expect(alpha).to_contain_text("1. 12")
        expect(bravo).to_contain_text("D · C. Longname-Deicer-0")
        expect(bravo).not_to_contain_text("F · T. Longname-Deicer-2")

        # Attendance can change outside DUCC, while Early Out uses the same save route.
        with case.app.app_context():
            operation = db.session.get(SortDateOperation, case.operation_id)
            night = StaffingUnit.query.filter_by(unit_type="sort", name="Night").one()
            db.session.add(StaffingDailyAttendance(
                person_id=case.people[1], sort_date_operation_id=operation.id,
                attendance_date=operation.sort_date, sort_unit_id=night.id, status="call_in",
            ))
            db.session.commit()
        tick(tv, 5000)
        expect(alpha.locator(".ucc-person-unavailable")).to_contain_text("Longname-Deicer-1")

        case.ready(work, "/neosubzero/ucc")
        early = work.locator('[data-ucc-early-out] .position-1')
        assign("driver", case.people[0], early)
        early.locator('[data-ucc-crew-edit="driver"]').click()
        tick(tv, 5000)
        expect(board.locator(".ucc-tv-early")).to_contain_text("C. Longname-Deicer-0")

        # Submit a real stale version and verify that neither revision nor TV changes.
        form = early.locator('[data-ucc-crew-form="driver"]')
        form.locator('[name="expected_version"]').evaluate("(e) => e.value = 'stale'")
        with work.expect_response(lambda r: r.request.method == "POST") as rejected:
            form.evaluate("(e) => { e.querySelector('select').value = ''; e.requestSubmit(); }")
        self.assertEqual(rejected.value.status, 409)
        expect(form.locator("select")).to_have_value("")
        expect(work.locator("[data-ucc-feedback]")).to_contain_text("changed")
        self.assertFalse(tick(tv, 5000)["changed"])
        expect(board.locator(".ucc-tv-early")).to_contain_text("C. Longname-Deicer-0")

        # Failed changed-fragment downloads must show degraded status and retry.
        failures.add("fragment")
        assign_truck = early.locator('[data-ucc-crew-form="truck"] select')
        assign_truck.select_option("UCC-831")
        expect(early.locator("[data-ucc-management-save]")).to_have_text("Saved")
        for _ in range(3):
            with tv.expect_response(lambda r: is_fragment(r.request) and r.status == 503):
                self.assertTrue(tick(tv, 5000)["changed"])
        expect(board.get_by_role("status")).to_contain_text("reconnecting")
        expect(board.locator(".ucc-tv-early")).not_to_contain_text("UCC-831")
        failures.remove("fragment")
        tick(tv, 5000)
        expect(board.locator(".ucc-tv-early")).to_contain_text("UCC-831")
        expect(board.locator("[data-ucc-tv-refresh-status]")).to_be_hidden()
        expect(board).to_have_attribute("data-ucc-tv-theme", "light")
        self.assertEqual(navigations, [])
        self.assertEqual(tv.url, case.origin + "/neosubzero/ucc")
        tick(second, 5000)
        expect(second.locator(".ucc-tv-early")).to_contain_text("UCC-831")

        # A revision-network failure retries through the same scheduler.
        failures.add("revision")
        self.assertFalse(tick(tv, 5000)["ok"])
        failures.remove("revision")
        self.assertFalse(tick(tv, 5000)["changed"])

        # A fresh application instance reads the same durable setting from disk.
        restarted = create_app(type("RestartedConfig", (), dict(case.app.config)))
        with restarted.app_context():
            gateway = Gateway.query.filter_by(code="RFD").one()
            self.assertEqual(live_screen_refresh_value(gateway, "neosubzero.ucc_tv").effective_interval_ms, 5000)
            db.session.remove()
            db.engine.dispose()

        revisions = [request for request in requests if is_revision(request)]
        fragments = [request for request in requests if is_fragment(request) and request.response().ok]
        print(json.dumps({
            "engine": engine, "unchanged_revision_requests": 2,
            "unchanged_response_body_bytes": unchanged_bytes,
            "revision_requests": len(revisions), "successful_fragments": len(fragments),
            "sample_changed_fragment_body_bytes": fragments[0].sizes()["responseBodySize"],
        }))

    def test_chromium_two_sessions(self):
        self.exercise("chromium")

    def test_webkit_two_sessions(self):
        self.exercise("webkit")
