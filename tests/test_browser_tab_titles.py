import unittest

from flask import render_template_string

from app import create_app


class BrowserTabTitleTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "test-browser-title-secret",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(config, auto_bootstrap=False)

    def assert_browser_title(self, path, expected):
        with self.app.test_request_context(path):
            rendered = render_template_string(
                "{% extends 'base.html' %}{% block content %}{% endblock %}"
            )

        self.assertIn(f"<title>{expected}</title>", rendered)

    def test_representative_pages_use_stable_page_and_owner_titles(self):
        cases = {
            "/": "Sign In | NeoApps",
            "/portal": "NeoPortal | NeoApps",
            "/nodes": "Node Directory | NeoApps",
            "/portal/manage": "Portal Management | NeoApps",
            "/admin/users/all": "All Users | NeoApps",
            "/rfd?operation_id=26": "RFD | NeoGateway",
            "/motherbrain/manage-sort?operation_id=26": "Manage Sort | NeoGateway",
            "/motherbrain/master-schedule/44": "Master Schedule | NeoGateway",
            "/motherbrain/operations/26/alp/arrival": "Arrival Planning | NeoGateway",
            "/motherbrain/operations/26/alp/departure": "Departure Planning | NeoGateway",
            "/motherbrain/parking-plan/26": "Parking Plan | NeoMotherBrain",
            "/motherbrain/parking-rules": "Parking Rules | NeoMotherBrain",
            "/neoermac/view-outbound": "Outbound | NeoErmac",
            "/neoermac/door-view?door=9": "Door View | NeoErmac",
            "/neosektor/tunnel-conductor": "Tunnel Conductor | NeoSektor",
            "/neosektor/live-counts": "Live Counts | NeoSektor",
            "/neosektor/ebm": "EBM | NeoSektor",
            "/neosektor/wbm": "WBM | NeoSektor",
            "/neoscorpion/fuel-dispatch": "Fuel Dispatch | NeoScorpion",
            "/neostaffing/people": "People | NeoStaffing",
            "/neostaffing/org-chart": "Org Chart | NeoStaffing",
        }

        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assert_browser_title(path, expected)

    def test_dynamic_record_paths_never_become_browser_titles(self):
        self.assert_browser_title(
            "/motherbrain/operations/994/missions/451",
            "Manage Sort | NeoGateway",
        )
        self.assert_browser_title(
            "/portal/manage/users/27",
            "User Management | NeoApps",
        )


if __name__ == "__main__":
    unittest.main()
