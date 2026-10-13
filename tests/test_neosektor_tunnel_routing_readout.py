"""Focused presentation contracts using the existing canonical routing fixture."""
import unittest
from datetime import date, datetime, time

from tests import test_neosektor_routes as fixtures
from tests.html_contracts import Element, document


class TunnelRoutingReadoutTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.NeoSektorRoutesTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture._login_approved_user(role="simulator")

    def assert_readout(self, routes):
        response = self.fixture.client.get("/neosektor/tunnel-conductor")
        self.assertEqual(response.status_code, 200)
        panel = document(response).one(cls="tunnel-counts-panel")
        children = [child for child in panel.children if isinstance(child, Element)]
        self.assertIn("tunnel-driver-routing", children[0].attrs["class"])
        self.assertEqual(children[1].text.strip(), "Ballmat Counts")
        labels = panel.findall(**{"data-tunnel-driver-route": None})
        self.assertEqual([label.attrs["data-tunnel-driver-route"] for label in labels], ["first", "second"])
        for label in labels:
            route = routes[label.attrs["data-tunnel-driver-route"]]
            expected = (f'ROUTING {route["direction"].upper()}'
                        if route["display_state"] == "route" else route["display_message"])
            self.assertEqual(label.text.strip(), expected)

    def test_initial_lifecycle_messages_match_driver_routing(self):
        payload = self.fixture.client.get("/neosektor/tunnel-conductor/state").get_json()
        routes = payload["state"]["routing"]["routes"]
        self.assertEqual(routes["first"]["display_state"], "not_arrived")
        self.assert_readout(routes)

    def test_initial_manual_routes_and_live_override_changes(self):
        fixture = self.fixture
        day = date(2026, 10, 12)
        operation = fixture._add_sort_operation(day)
        operation.generated_by_user_id = fixtures.User.query.first().id
        fixture.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime.combine(day, time(23))
        fixture._set_sort_window("night", time(0), time(23, 59, 59))
        for wave in ("1", "2"):
            fixtures.db.session.add(fixtures.SortDateMission(
                sort_date_operation=operation, sort_date=day, gateway_code=fixture.gateway.code,
                sort_name="night", mission_type="arrival", mission_source="manual", wave=wave,
                flight_number=f"WAVE{wave}", origin="OAK", destination="RFD",
                actual_block_in_datetime_utc=datetime(2026, 10, 12, 22),
            ))
        fixtures.db.session.commit()
        for wave in ("first", "second"):
            self.assertEqual(fixture.client.post("/neosektor/tunnel-conductor/wave",
                             json={"wave": wave, "value": 8}).status_code, 200)
        settings_url = "/neosektor/tunnel-conductor/settings"
        self.assertEqual(fixture.client.post(settings_url, json={
            "first_override": "east", "second_override": "west"}).status_code, 200)
        initial = fixture.client.get("/neosektor/tunnel-conductor/state").get_json()
        routes = initial["state"]["routing"]["routes"]
        self.assertEqual([routes[key]["display_state"] for key in ("first", "second")], ["route", "route"])
        self.assertEqual([routes[key]["direction"] for key in ("first", "second")], ["east", "west"])
        self.assert_readout(routes)

        fixture.client.post(settings_url, json={"first_override": "west", "second_override": "east"})
        updated = fixture.client.get("/neosektor/tunnel-conductor/state",
                                     query_string={"revision": initial["revision"]}).get_json()
        self.assertTrue(updated["changed"])
        routes = updated["state"]["routing"]["routes"]
        self.assertEqual([routes[key]["direction"] for key in ("first", "second")], ["west", "east"])
        canonical = fixture.client.get("/neosektor/driver-routing/state").get_json()
        self.assertEqual(updated["state"]["routing"], canonical["state"]["routing"])
        self.assert_readout(routes)
        fixture.client.post("/neosektor/tunnel-conductor/wave", json={"wave": "first", "value": 0})
        routes = fixture.client.get("/neosektor/tunnel-conductor/state").get_json()["state"]["routing"]["routes"]
        self.assertEqual(routes["first"]["display_state"], "all_in")
        self.assert_readout(routes)
