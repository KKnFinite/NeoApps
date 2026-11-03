"""Presentation navigation retains the established DUCC authorization boundary."""
import unittest
from unittest.mock import patch
from tests import test_neosubzero_ucc as fixtures


class MobileRampRouteTest(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.NeoSubZeroUccTest();self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def test_direct_ramp_mode_normalizes_only_known_ramps_without_operational_changes(self):
        f=self.fixture;client=f.app.test_client();f._login(client,f._user('mobile-ramp-reader','watcher'))
        with patch('app.neonodes.neosubzero.routes.current_neosubzero_operation',return_value=f.operation),patch(
                'app.neonodes.neosubzero.routes.neosubzero_weather_context',return_value=f._weather_context_fixture()):
            before=fixtures.neosubzero_ucc_revision(f.gateway,f.operation)
            for query,expected in (('view=ramp&ramp=bravo','Bravo'),('view=ramp&ramp=NOT-A-RAMP','Alpha')):
                response=client.get('/neosubzero/ucc?'+query)
                self.assertEqual(response.status_code,200)
                self.assertIn(b'data-ucc-mobile-view="ramp"',response.data)
                self.assertIn(('data-ucc-selected-ramp="'+expected+'"').encode(),response.data)
                self.assertIn(b'aria-label="DUCC ramp selector"',response.data)
                self.assertNotIn(b'data-ucc-queue-form',response.data)
                self.assertNotIn(b'data-ucc-drag-assignee',response.data)
            self.assertEqual(fixtures.neosubzero_ucc_revision(f.gateway,f.operation),before)
            denied=client.post('/neosubzero/ucc?view=ramp',data={'ramp':'Alpha','position_number':1,'team_role':'driver'})
            self.assertEqual(denied.status_code,403)

    def test_mobile_navigation_does_not_grant_an_unpermitted_page(self):
        f=self.fixture;client=f.app.test_client();f._login(client,f._user('mobile-ramp-nav','watcher'))
        from app.services import permission_rules
        original=permission_rules.user_can
        def allowed(key,*args,**kwargs):
            if key in {'neosubzero.ucc.view','neosubzero.ucc.edit'}:return False
            return original(key,*args,**kwargs)
        with patch('app.neonodes.neosubzero.routes.current_neosubzero_operation',return_value=f.operation),patch(
                'app.services.permission_rules.user_can',side_effect=allowed),patch(
                'app.neonodes.neosubzero.routes.user_can',side_effect=allowed),patch('app.user_can',side_effect=allowed):
            self.assertEqual(client.get('/neosubzero/ucc?view=ramp').status_code,302)
            page=client.get('/neosubzero/coordinator?ramp=Alpha')
            self.assertEqual(page.status_code,200)
            self.assertNotIn(b'neosubzero-mobile-ramp-link',page.data)
