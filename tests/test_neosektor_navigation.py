"""Exact pre-consolidation navigation contracts; no database fixture needed."""

import unittest
from unittest.mock import patch

from flask import Flask
from app.neonodes.neosektor import routes


# Snapshot from cce03ed: labels/descriptions are intentionally presentation-specific.
EXPECTED = {'NEOSEKTOR_PAGES': (('TUNNEL CONDUCTOR',
                      'neosektor.tunnel_conductor',
                      'neosektor.conductor.view',
                      'neosektor.tunnel_conductor.edit',
                      'Tunnel Conductor live count controls.'),
                     ('SETTINGS',
                      'neosektor.settings',
                      'neosektor.settings.view',
                      'neosektor.settings.edit',
                      'NeoSektor application settings.'),
                     ('EBM',
                      'neosektor.ebm',
                      'neosektor.ebm.view',
                      'neosektor.ebm.edit',
                      'East Ballmat Operations count entry.'),
                     ('WBM',
                      'neosektor.wbm',
                      'neosektor.wbm.view',
                      'neosektor.wbm.edit',
                      'West Ballmat Operations count entry.'),
                     ('DISCHARGE',
                      'neosektor.discharge',
                      'neosektor.discharge.view',
                      'neosektor.discharge.edit',
                      'NeoSektor ULD request discharge queue.'),
                     ('DRIVER ROUTING',
                      'neosektor.driver_routing',
                      'neosektor.driver_routing.view',
                      None,
                      'Driver routing foundation.')),
 'NEOSEKTOR_INTERNAL_MENU': (('Live Counts', 'neosektor.live_counts', 'neosektor.live_counts.view'),
                             ('Settings', 'neosektor.settings', 'neosektor.settings.view'),
                             ('Tunnel Conductor', 'neosektor.tunnel_conductor', 'neosektor.conductor.view'),
                             ('East Ballmat', 'neosektor.ebm', 'neosektor.ebm.view'),
                             ('West Ballmat', 'neosektor.wbm', 'neosektor.wbm.view'),
                             ('Driver Routing', 'neosektor.driver_routing', 'neosektor.driver_routing.view'),
                             ('Discharge', 'neosektor.discharge', 'neosektor.discharge.view')),
 'NEOSEKTOR_MOBILE_DASHBOARD': (('Live Counts',
                                 'neosektor.live_counts',
                                 'neosektor.live_counts.view',
                                 'live-counts',
                                 'Live flow and bay status.'),
                                ('Tunnel Conductor',
                                 'neosektor.tunnel_conductor',
                                 'neosektor.conductor.view',
                                 'tunnel',
                                 'Tunnel counts and down timer.'),
                                ('Settings',
                                 'neosektor.settings',
                                 'neosektor.settings.view',
                                 'settings',
                                 'NeoSektor application settings.'),
                                ('EBM',
                                 'neosektor.ebm',
                                 'neosektor.ebm.view',
                                 'ebm',
                                 'East ballmat count entry.'),
                                ('WBM',
                                 'neosektor.wbm',
                                 'neosektor.wbm.view',
                                 'wbm',
                                 'West ballmat count entry.'),
                                ('Discharge',
                                 'neosektor.discharge',
                                 'neosektor.discharge.view',
                                 'discharge',
                                 'ULD request queue.'),
                                ('Driver Routing',
                                 'neosektor.driver_routing',
                                 'neosektor.driver_routing.view',
                                 'driver-routing',
                                 'Driver need and route board.'))}


class NeoSektorNavigationTest(unittest.TestCase):
    def test_presentations_and_page_lookup_match_original_metadata(self):
        for name, expected in EXPECTED.items():
            self.assertEqual(getattr(routes, name), expected, name)
        self.assertEqual(len(routes.NEOSEKTOR_PAGE_DEFINITIONS), 7)
        self.assertEqual(len({p.endpoint for p in routes.NEOSEKTOR_PAGE_DEFINITIONS}), 7)
        for row in EXPECTED["NEOSEKTOR_PAGES"]:
            self.assertEqual(routes._page_by_title(row[0]), dict(zip(
                ("label", "endpoint", "view_permission", "edit_permission", "description"), row,
            )))
        for unknown in ("LIVE COUNTS", "Tunnel Conductor", "missing"):
            with self.assertRaises(ValueError):
                routes._page_by_title(unknown)

    def test_every_permission_subset_preserves_order_active_state_and_preloading(self):
        app = Flask(__name__)
        app.add_url_rule("/", endpoint="neosektor.ebm", view_func=lambda: "")
        menu = EXPECTED["NEOSEKTOR_INTERNAL_MENU"]
        dashboard = EXPECTED["NEOSEKTOR_MOBILE_DASHBOARD"]
        pages = EXPECTED["NEOSEKTOR_PAGES"]
        expected_preload = [row[2] for group in (menu, dashboard, pages) for row in group]
        keys = [row[2] for row in menu]
        with app.test_request_context("/"):
            # Includes all-denied, all-allowed and every mixed permission subset.
            for mask in range(1 << len(keys)):
                allowed = {key for index, key in enumerate(keys) if mask & (1 << index)}
                preloads = []
                def preload(values):
                    preloads.append(list(values))
                def can(permission):
                    self.assertTrue(preloads)  # Preload must precede checks.
                    return permission in allowed
                with self.subTest(mask=mask), patch.object(routes, "preload_permission_rules", side_effect=preload), patch.object(routes, "user_can", side_effect=can):
                    self.assertEqual(routes._visible_neosektor_menu_items(), [
                        {"label": label, "endpoint": endpoint, "active": endpoint == "neosektor.ebm"}
                        for label, endpoint, permission in menu if permission in allowed
                    ])
                    self.assertEqual(routes._visible_neosektor_mobile_dashboard_items(), [
                        {"label": label, "endpoint": endpoint, "key": key, "description": description,
                         "active": endpoint == "neosektor.ebm"}
                        for label, endpoint, permission, key, description in dashboard if permission in allowed
                    ])
                    self.assertEqual(routes._visible_neosektor_page_items(), [
                        row for row in pages if row[2] in allowed
                    ])
                    self.assertEqual(preloads, [expected_preload] * 3)
