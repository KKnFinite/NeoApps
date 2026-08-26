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
            "neoermac.view_outbound_state",
            "neomotherbrain.parking_plan_live_state_endpoint",
            "neomotherbrain.planning_live_state",
            "neoscorpion.fuel_assignments_revision",
            "neoscorpion.fuel_dispatch_revision",
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

    def test_view_outbound_state_is_lightweight_but_page_is_not(self):
        self.assertFalse(
            is_lightweight_live_state_request("neoermac.view_outbound", "GET")
        )
        self.assertFalse(
            is_lightweight_live_state_request(
                "neoermac.view_outbound",
                "GET",
                {"revision": "legacy"},
            )
        )
        self.assertTrue(
            is_lightweight_live_state_request(
                "neoermac.view_outbound_state",
                "GET",
            )
        )
        self.assertFalse(
            is_lightweight_live_state_request(
                "neoermac.view_outbound_state",
                "POST",
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
        outbound = lightweight_live_state_scope_spec(
            "neoermac.view_outbound_state"
        )
        sektor = lightweight_live_state_scope_spec("neosektor.live_counts_state")
        scorpion = lightweight_live_state_scope_spec(
            "neoscorpion.fuel_assignments_revision"
        )
        dispatch = lightweight_live_state_scope_spec(
            "neoscorpion.fuel_dispatch_revision"
        )

        self.assertEqual(parking["node_code"], "motherbrain")
        self.assertEqual(parking["operation_id"], 42)
        self.assertFalse(parking["include_current_ermac_operation"])
        self.assertEqual(door["node_code"], "ermac")
        self.assertTrue(door["include_current_ermac_operation"])
        self.assertEqual(outbound["node_code"], "ermac")
        self.assertTrue(outbound["include_current_ermac_operation"])
        self.assertEqual(sektor["node_code"], "sektor")
        self.assertIsNone(sektor["operation_id"])
        self.assertEqual(scorpion["node_code"], "scorpion")
        self.assertIsNone(scorpion["operation_id"])
        self.assertFalse(scorpion["include_current_ermac_operation"])
        self.assertEqual(dispatch["node_code"], "scorpion")
        self.assertIsNone(dispatch["operation_id"])
        self.assertFalse(dispatch["include_current_ermac_operation"])
        self.assertIsNone(
            lightweight_live_state_scope_spec(
                "neomotherbrain.parking_plan_operation"
            )
        )


if __name__ == "__main__":
    unittest.main()
