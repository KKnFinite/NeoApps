import unittest
from datetime import time, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

from playwright.sync_api import expect
from app.extensions import db
from app.models import Gateway, LiveScreenRefreshSetting, NeoErmacDoorPull, NeoSubZeroDepartureDeiceEvent, SortDateMission, User
from tests.browser import test_mobile_drawer as fixtures
from tests.test_neoraiden import seed_sort
from tests.neoraiden_fixtures import record_pull_roster
from tests.test_neorain_sort_refresh import configure_sort_window


class CommandCenterBrowser(unittest.TestCase):
    def test_authorized_navigation_refresh_and_board(self):
        fixture = fixtures.MobileDrawerBrowserTest
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        with fixture.app.app_context():
            gateway = Gateway.query.one()
            op = seed_sort(gateway)
            op_id = op.id
            record_pull_roster(op)
            mission = SortDateMission.query.filter_by(sort_date_operation_id=op_id, flight_number="FX101").one()
            mission_id = mission.id
            _, start = configure_sort_window(gateway, op.sort_date)
            db.session.add(LiveScreenRefreshSetting(gateway_id=gateway.id, screen_key="neoraiden.index", interval_seconds=15))
            start_utc = start.replace(tzinfo=ZoneInfo("America/Chicago")).astimezone(timezone.utc).replace(tzinfo=None)
            db.session.add(NeoSubZeroDepartureDeiceEvent(sort_date_operation_id=op_id, sort_date_mission_id=mission_id,
                tail_number=mission.assigned_tail_number, status="configured", treatment_plan="one_type_i",
                pass1_started_at_utc=start_utc))
            changing = SortDateMission(sort_date_operation_id=op.id, sort_date=op.sort_date, sort_name=op.sort_name,
                gateway_code=gateway.code, mission_type="arrival", flight_number="UPS1361",
                origin="SDF", destination=gateway.code, arrival_status="en_route",
                planned_datetime_utc=start_utc + timedelta(minutes=55),
                eta_datetime_utc=start_utc + timedelta(minutes=50))
            overnight = SortDateMission(sort_date_operation_id=op.id, sort_date=op.sort_date, sort_name=op.sort_name,
                gateway_code=gateway.code, mission_type="arrival", flight_number="UPS0927",
                origin="SDF", destination=gateway.code, planned_datetime_utc=start_utc - timedelta(hours=20))
            db.session.add_all([changing, overnight])
            db.session.commit()
            changing_id = changing.id
            fixture.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = start
        evidence = Path("instance/browser-evidence/gateway-shell")
        evidence.mkdir(parents=True,exist_ok=True)
        for engine in ("chromium","webkit"):
            with self.subTest(engine=engine):
                browser = getattr(fixture.pw,engine).launch()
                try:
                    page = browser.new_page(viewport=dict(width=1366,height=768), service_workers="block")
                    page.clock.install(time=start.replace(tzinfo=ZoneInfo("America/Chicago")))
                    fixture().login(page,username="drawer-watcher")
                    fixture().ready(page,"/rfd")
                    for width, height in ((390,844),(1366,768)):
                        page.set_viewport_size(dict(width=width,height=height))
                        launcher = page.get_by_role("link",name="Open NeoRaiden")
                        expect(launcher).to_be_visible()
                        expect(launcher.locator(".node-word:visible")).to_have_css("color", "rgb(159, 201, 249)")
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width+1)
                        page.screenshot(path=evidence / f"launcher-{engine}-{width}.png")
                    page.get_by_role("link",name="Open NeoRaiden").click()
                    expect(page.locator("[data-raiden]")).to_be_visible()
                    page.locator("[data-sort-choice]").select_option(index=1)
                    arrivals = page.locator("#rd-arrivals [data-mission]")
                    expect(arrivals.locator(".rd-flight")).to_have_text(["UPS1361", "UPS0927"])
                    expect(arrivals.first).to_contain_text("ETA 21:50")
                    variance = page.locator(f'#rd-mission-{changing_id} [data-field="variance"]')
                    expect(variance).to_have_text("-5")
                    expect(arrivals.last).to_contain_text("STA 01:00")
                    departure = page.locator(f'#rd-mission-{mission_id}')
                    expect(departure.locator('[data-field="pure"] .rd-value')).to_have_text("00:02")
                    expect(departure.locator(".rd-deice")).to_have_attribute("data-deice-state", "deicing")
                    expect(departure.locator(".rd-deice")).to_have_css("animation-name", "rd-deice-breathe")
                    expect(page.locator("[data-countdown],[data-refresh],[data-interval]")).to_have_count(0)
                    expect(page.locator("[data-raiden]")).to_have_attribute("data-interval-ms", "15000")
                    expect(page.locator("[data-refresh-status]")).to_be_empty()
                    with page.expect_response(lambda r:"/neoraiden/state" in r.url) as first:
                        page.clock.fast_forward(15000)
                    self.assertEqual(first.value.status,304)
                    with fixture.app.app_context():
                        m=SortDateMission.query.filter_by(sort_date_operation_id=op_id, flight_number="FX202").one()
                        m.assigned_tail_number="N" + engine.upper()
                        m.ramp_load_completed_at_utc = start_utc + timedelta(minutes=90)
                        m = SortDateMission.query.filter_by(sort_date_operation_id=op_id, flight_number="UPS1361").one()
                        m.eta_datetime_utc = start_utc + timedelta(hours=4, minutes=15)
                        NeoErmacDoorPull.query.filter_by(sort_date_mission_id=mission_id, door="D4").one().actual_pure_pull_time_local = None
                        event = NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=mission_id).one()
                        event.status, event.clear_outcome = "cleared", "sprayed"
                        event.pass1_ended_at_utc = start_utc + timedelta(minutes=4)
                        event.radio_state = "complete"
                        event.radio_completed_at_utc = start_utc + timedelta(minutes=9)
                        event.radio_completed_by_user_id = User.query.filter_by(username="drawer-watcher").one().id
                        db.session.commit()
                    page.evaluate("window.raidenRefreshMarker = true")
                    with page.expect_response(lambda r:"/neoraiden/state" in r.url, timeout=22000) as updated:
                        page.clock.fast_forward(15000)
                    self.assertEqual(updated.value.status,200)
                    self.assertEqual(updated.value.request.headers["x-raiden-automatic"], "1")
                    expect(page.locator("[data-board]")).to_contain_text("N"+engine.upper())
                    expect(page.locator('#rd-departures .rd-wide-departures [data-field="ramp"] .rd-value').first).to_have_text("22:30")
                    expect(arrivals.locator(".rd-flight")).to_have_text(["UPS0927", "UPS1361"])
                    expect(arrivals.last).to_contain_text("ETA 01:15")
                    expect(variance).to_have_text("+200")
                    expect(variance.locator(".rd-value")).to_have_attribute("data-value-color", "late")
                    expect(departure.locator('[data-field="pure"] .rd-value')).to_be_empty()
                    expect(departure.locator('[data-field="mix"] .rd-value')).to_have_text("00:57")
                    expect(departure.locator(".rd-deice")).to_have_attribute("title", "Completed 21:04")
                    expect(departure.locator(".rd-deice")).to_have_css("animation-name", "none")
                    self.assertTrue(page.evaluate("window.raidenRefreshMarker"))
                    page.set_viewport_size(dict(width=390,height=844))
                    page.locator('[data-show-missions="departures"]').click()
                    pair = page.locator(f'[data-paired-mission="{mission_id}"] [data-pair="time"]')
                    expect(pair.locator('[data-field="time"]')).to_be_visible()
                    expect(pair.locator('[data-field="status"]')).to_be_visible()
                    for minutes, expected, tone in ((6, "+6", "late"), (-2, "-2", "on-time"), (0, "0", "on-time"), (None, None, None)):
                        with fixture.app.app_context():
                            m = db.session.get(SortDateMission, mission_id)
                            m.departure_status = "departed" if minutes is not None else "scheduled"
                            m.actual_block_out_datetime_utc = (m.planned_datetime_utc + timedelta(minutes=minutes)
                                                               if minutes is not None else None)
                            db.session.commit()
                        with page.expect_response(lambda r:"/neoraiden/state" in r.url and r.status == 200):
                            page.clock.fast_forward(15000)
                        paired_row = page.locator(f'[data-paired-mission="{mission_id}"]')
                        marker = paired_row.get_by_role("img", name="No Return confirmed")
                        if expected is None:
                            expect(pair.locator('[data-field="time"]')).to_be_visible()
                            expect(pair.locator('[data-field="status"]')).to_have_text("scheduled")
                            expect(pair.locator('[data-field="block"]')).to_have_count(0)
                            expect(marker).to_have_count(0)
                            expect(paired_row.locator('[data-field="flight"] .rd-value')).not_to_have_attribute("data-value-color", "on-time")
                        else:
                            expect(pair.locator('[data-field="time"], [data-field="status"]')).to_have_count(0)
                            expect(pair.locator('[data-field="block"]')).to_be_visible()
                            value = pair.locator('[data-field="variance"] .rd-value')
                            expect(value).to_have_text(expected)
                            expect(value).to_have_attribute("data-value-color", tone)
                            expect(paired_row.locator('[data-field="flight"] .rd-value')).to_have_attribute("data-value-color", tone)
                            expect(marker).to_be_visible()
                    for w,h in ((1366,768),(1920,1080),(1024,768),(768,1024),(390,844)):
                        page.set_viewport_size(dict(width=w,height=h))
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), w+1)
                        if w>900:
                            page.locator(".operational-sidebar-utilities [data-operational-board-toggle]").click()
                            box=page.locator("[data-raiden]").bounding_box()
                            self.assertGreaterEqual(box["width"],w-24)
                            page.screenshot(path=evidence / f"raiden-{engine}-{w}-board.png")
                            page.locator(".operational-board-exit").click()
                        page.screenshot(path=evidence / f"raiden-{engine}-{w}.png")
                    page.set_viewport_size(dict(width=1280,height=720))
                    page.get_by_role("button",name="TV Mode",exact=True).click()
                    expect(page.locator(".rd-tv")).to_be_visible()
                    page.route("**/neoraiden/state*",lambda route:route.fulfill(status=503,body=""))
                    automatic_requests = []
                    page.on("request", lambda req: automatic_requests.append(req.url)
                            if "/neoraiden/state" in req.url else None)
                    page.clock.set_system_time((start + timedelta(hours=8)).replace(tzinfo=ZoneInfo("America/Chicago")))
                    page.clock.fast_forward(61000)
                    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
                    expect(page.locator("[data-refresh-status]")).to_have_text("Outside Sort hours")
                    self.assertEqual(automatic_requests, [])
                    expect(page.locator(".rd-tv")).to_be_visible()
                    page.clock.set_system_time((start + timedelta(minutes=5)).replace(tzinfo=ZoneInfo("America/Chicago")))
                    with page.expect_response(lambda r:"/neoraiden/state" in r.url and r.status == 503):
                        page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
                    expect(page.locator("[data-stale]")).to_contain_text("last successful update")
                    self.assertEqual(page.locator("[data-mission]").count(),4)
                    page.unroute("**/neoraiden/state*")
                    page.route("**/neoraiden/state*",lambda route:route.fulfill(status=403,body=""))
                    with page.expect_response(lambda r:"/neoraiden/state" in r.url and r.status == 403):
                        page.clock.fast_forward(15000)
                    expect(page.locator("[data-board]")).to_be_empty()
                    expect(page.locator("[data-refresh-status]")).to_have_text("Live updates off")
                finally:
                    browser.close()
                    with fixture.app.app_context():
                        SortDateMission.query.filter_by(sort_date_operation_id=op_id, flight_number="UPS1361").one().eta_datetime_utc = start_utc + timedelta(minutes=50)
                        NeoErmacDoorPull.query.filter_by(sort_date_mission_id=mission_id, door="D4").one().actual_pure_pull_time_local = time(0, 2)
                        event = NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=mission_id).one()
                        event.status = "configured"
                        event.clear_outcome = event.pass1_ended_at_utc = event.radio_state = None
                        event.radio_completed_at_utc = event.radio_completed_by_user_id = None
                        db.session.commit()
