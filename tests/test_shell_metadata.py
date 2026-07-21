from types import SimpleNamespace
import unittest

from flask import request

from app import create_app
from app.services.browser_titles import browser_tab_title
from app.services.shell_metadata import resolve_shell_metadata


class ShellMetadataTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "test-shell-metadata-secret",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(config, auto_bootstrap=False)

    def metadata_for_path(self, path):
        with self.app.test_request_context(path):
            return resolve_shell_metadata(
                request,
                is_authenticated=True,
                user_last_name="Kessler",
                default_gateway_code="RFD",
            )

    def test_representative_routes_keep_existing_shell_labels(self):
        cases = {
            "/portal": ("PORTAL", "apps", "NeoApps", "NeoPortal | NeoApps"),
            "/rfd": ("RFD", "gateway", "NeoGateway", "RFD | NeoGateway"),
            "/motherbrain/parking-plan": (
                "Parking",
                "motherbrain",
                "NeoMotherBrain",
                "Parking Plan | NeoMotherBrain",
            ),
            "/neoermac/building-lineup": (
                "LINEUP",
                "ermac",
                "NeoErmac",
                "Building Lineup | NeoErmac",
            ),
            "/neosektor/live-counts": (
                "COUNTS",
                "sektor",
                "NeoSektor",
                "Live Counts | NeoSektor",
            ),
            "/neoscorpion/fuel-dispatch": (
                "DISPATCH",
                "scorpion",
                "NeoScorpion",
                "Fuel Dispatch | NeoScorpion",
            ),
            "/neostaffing/people": (
                "People",
                "staffing",
                "NeoStaffing",
                "People | NeoStaffing",
            ),
        }

        for path, (label, key, name, title) in cases.items():
            with self.subTest(path=path):
                with self.app.test_request_context(path):
                    metadata = resolve_shell_metadata(
                        request,
                        is_authenticated=True,
                        user_last_name="Kessler",
                        default_gateway_code="RFD",
                    )
                    self.assertEqual(browser_tab_title(request), title)
                self.assertEqual(metadata["mobile_shell_label"], label)
                self.assertEqual(metadata["mobile_shell_key"], key)
                self.assertEqual(metadata["mobile_shell_name"], name)

    def test_nested_node_pages_keep_desktop_and_mobile_labels(self):
        cases = {
            "/neosektor/settings": ("SETTINGS", "DASHBOARD"),
            "/neoermac/door-view": ("DOOR VIEW", "DOORS"),
            "/neoscorpion/fuel-dispatch": ("FUEL DISPATCH", "DISPATCH"),
        }

        for path, (desktop_label, mobile_label) in cases.items():
            with self.subTest(path=path):
                metadata = self.metadata_for_path(path)
                self.assertEqual(metadata["node_current_label"], desktop_label)
                self.assertEqual(metadata["mobile_shell_label"], mobile_label)

        staffing = self.metadata_for_path("/neostaffing/people")
        self.assertEqual(staffing["neostaffing_current_label"], "People")
        self.assertEqual(staffing["mobile_shell_label"], "People")

    def test_ballmat_side_and_motherbrain_page_labels_remain_route_specific(self):
        east = self.metadata_for_path("/neosektor/ballmat?side=east")
        parking = self.metadata_for_path("/motherbrain/parking-plan")

        self.assertTrue(east["is_neosektor_ebm_page"])
        self.assertFalse(east["is_neosektor_wbm_page"])
        self.assertEqual(east["node_current_label"], "EBM")
        self.assertEqual(parking["node_current_label"], "Parking Plan")
        self.assertEqual(parking["mobile_shell_label"], "Parking")

    def test_coming_soon_node_identity_and_unknown_fallback_remain_stable(self):
        future_request = SimpleNamespace(
            path="/neoreptile",
            blueprint="neoreptile",
            endpoint="neoreptile.index",
            args={},
        )
        future_metadata = resolve_shell_metadata(
            future_request,
            is_authenticated=True,
            default_gateway_code="RFD",
        )
        unknown_request = SimpleNamespace(
            path="/unknown-page",
            blueprint=None,
            endpoint=None,
            args={},
        )
        unknown_metadata = resolve_shell_metadata(
            unknown_request,
            is_authenticated=True,
            default_gateway_code="RFD",
        )

        self.assertTrue(future_metadata["uses_node_header"])
        self.assertEqual(future_metadata["node_header_key"], "reptile")
        self.assertEqual(future_metadata["mobile_shell_name"], "NeoReptile")
        self.assertFalse(unknown_metadata["uses_node_header"])
        self.assertEqual(unknown_metadata["mobile_shell_label"], "PORTAL")
        self.assertEqual(unknown_metadata["mobile_home_endpoint"], "auth.portal_dashboard")

    def test_base_template_consumes_context_metadata_without_path_classifier(self):
        with open("app/templates/base.html", encoding="utf-8") as template_file:
            template = template_file.read()

        self.assertNotIn("{% set motherbrain_current_label", template)
        self.assertNotIn("{% set node_header_key", template)
        self.assertNotIn("{% set neosektor_current_label", template)
        self.assertIn("{{ node_current_label }}", template)
        self.assertIn("{{ mobile_shell_label }}", template)


if __name__ == "__main__":
    unittest.main()
