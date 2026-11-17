"""Navigation visibility must not change direct-route authority or workflows."""
import unittest
from unittest.mock import patch

from app.extensions import db
from app.neonodes.neosubzero.routes import CRYOCHECK_MENU_ITEM, navigation, neosubzero_navigation_sections
from app.services.neosubzero_ucc import set_neosubzero_ucc_assignment
from tests import test_neosubzero_ucc as fixtures


INTERNAL_LINKS = [
    ('DUCC', 'ucc'), ('Deice Dispatch', 'operations'), ('Deicer', 'deicer-mobile'),
    ('Deice Log', 'deice-log'), ('Deicer Management', 'deicer-management'),
    ('Truck Management', 'truck-management'), ('Settings', 'settings'),
]
LABELS = [label for label, _ in INTERNAL_LINKS[:-1]] + ['CryoCheck', 'Settings']
HREFS = ['/neosubzero/' + path for _, path in INTERNAL_LINKS[:-1]] + [
    'https://cryocheck.onrender.com', '/neosubzero/settings',
]


class SubZeroNavigationTest(unittest.TestCase):
    def test_exact_order_labels_and_shared_flat_compatibility(self):
        with patch('app.neonodes.neosubzero.routes.user_can', return_value=True):
            sections = neosubzero_navigation_sections()
            self.assertEqual([label for _heading, items in sections for label, _endpoint, _external in items], LABELS)
            self.assertEqual([heading for heading, _items in sections], [None])
            self.assertEqual(navigation()['neosubzero_menu_items'](), [item for _heading, items in sections for item in items])

    def test_permissions_filter_links_and_empty_headings(self):
        with patch('app.neonodes.neosubzero.routes.user_can', side_effect=lambda permission: permission == 'neosubzero.deicer_mobile.view'):
            self.assertEqual(neosubzero_navigation_sections(), [
                (None, [('Deicer', 'neosubzero.deicer_mobile', None), CRYOCHECK_MENU_ITEM]),
            ])
        with patch('app.neonodes.neosubzero.routes.user_can', side_effect=lambda permission: permission == 'neosubzero.callouts.view'):
            self.assertEqual(neosubzero_navigation_sections(), [
                (None, [('Deicer Management', 'neosubzero.callouts', None), CRYOCHECK_MENU_ITEM]),
            ])
        with patch('app.neonodes.neosubzero.routes.user_can', return_value=False):
            self.assertEqual(neosubzero_navigation_sections(), [])

    def test_authorized_routes_and_active_links_keep_existing_permissions(self):
        fixture = fixtures.NeoSubZeroUccTest()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        actor = fixture._user('nav-admin', 'grandmaster')
        actor.employee_id = fixture.permanent.employee_id
        set_neosubzero_ucc_assignment(fixture.operation, 'Alpha', 1, 'driver', fixture.permanent)
        db.session.commit()
        client = fixture.app.test_client()
        fixture._login(client, actor)
        weather = fixture._weather_context_fixture()
        with patch('app.neonodes.neosubzero.routes.current_neosubzero_operation', return_value=fixture.operation), patch('app.neonodes.neosubzero.routes.neosubzero_weather_context', return_value=weather):
            for label, path in INTERNAL_LINKS:
                with self.subTest(path=path):
                    response = client.get('/neosubzero/' + path)
                    self.assertEqual(response.status_code, 200)
                    html = response.get_data(as_text=True)
                    self.assertIn(f'href="/neosubzero/{path}" aria-label="{label}" title="{label}" aria-current="page"', html)
                    self.assertNotIn('href="/neosubzero/qualifications"', html)
                    self.assertNotIn('href="/neosubzero/coordinator', html)
                    self.assertIn('href="https://cryocheck.onrender.com" aria-label="CryoCheck" title="CryoCheck" target="_blank" rel="noopener noreferrer"', html)
                    # Pretreat may still be referenced by workflow controls, but never a navigation entry.
                    self.assertNotIn('aria-label="Pretreat"', html)
            self.assertEqual(client.get('/neosubzero/pretreat').status_code, 200)
            self.assertEqual(client.get('/neosubzero/qualifications').status_code, 302)
            with patch('app.neonodes.neosubzero.routes.permission_access', return_value={'can_view': False, 'can_edit': False}):
                self.assertEqual(client.get('/neosubzero/pretreat').status_code, 302)
                self.assertEqual(client.get('/neosubzero/qualifications').status_code, 403)
