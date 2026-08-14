import unittest

from app.services.operational_request_policy import (
    LIGHTWEIGHT_LIVE_STATE_ENDPOINTS,
    is_lightweight_live_state_request,
    lightweight_live_state_scope_spec,
)


class OperationalRequestPolicyTest(unittest.TestCase):
    def test_explicit_live_state_endpoints_are_lightweight_for_reads_only(self):
        expected = {
            "neoermac.door_view_state",
            "neoermac.upcoming_pulls_state",
            "neomotherbrain.parking_plan_live_state_endpoint",
            "neomotherbrain.planning_live_state",
            "neosektor.ballmat_state",
            "neosektor.discharge_state",
            "neosektor.driver_routing_state",
            "neosektor.live_counts_state",
            "neosektor.tunnel_conductor_state",
        }

        self.assertEqual(LIGHTWEIGHT_LIVE_STATE_ENDPOINTS, expected)
        for endpoint in expected:
            with self.subTest(endpoint=endpoint):
                self.assertTrue(
                    is_lightweight_live_state_request(endpoint, "GET")
                )
                self.assertFalse(
                    is_lightweight_live_state_request(endpoint, "POST")
                )

    def test_view_outbound_is_lightweight_only_for_revision_poll(self):
        endpoint = "neoermac.view_outbound"

        self.assertFalse(is_lightweight_live_state_request(endpoint, "GET"))
        self.assertFalse(
            is_lightweight_live_state_request(endpoint, "GET", {"revision": ""})
        )
        self.assertTrue(
            is_lightweight_live_state_request(
                endpoint,
                "GET",
                {"revision": "abc123"},
            )
        )

    def test_upcoming_pulls_page_remains_a_normal_lifecycle_request(self):
        self.assertFalse(
            is_lightweight_live_state_request("neoermac.upcoming_pulls", "GET")
        )

    def test_unlisted_operational_get_is_not_lightweight(self):
        self.assertFalse(
            is_lightweight_live_state_request(
                "neomotherbrain.parking_plan_operation",
                "GET",
            )
        )

    def test_scope_spec_maps_only_approved_live_endpoints(self):
        parking = lightweight_live_state_scope_spec(
            "neomotherbrain.parking_plan_live_state_endpoint",
            {"operation_id": 42},
        )
        door = lightweight_live_state_scope_spec("neoermac.door_view_state")
        sektor = lightweight_live_state_scope_spec("neosektor.live_counts_state")

        self.assertEqual(parking["node_code"], "motherbrain")
        self.assertEqual(parking["operation_id"], 42)
        self.assertFalse(parking["include_current_ermac_operation"])
        self.assertEqual(door["node_code"], "ermac")
        self.assertTrue(door["include_current_ermac_operation"])
        self.assertEqual(sektor["node_code"], "sektor")
        self.assertIsNone(sektor["operation_id"])
        self.assertIsNone(
            lightweight_live_state_scope_spec(
                "neomotherbrain.parking_plan_operation"
            )
        )


if __name__ == "__main__":
    unittest.main()
