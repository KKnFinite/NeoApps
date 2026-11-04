"""Real-session revision reads and authority loss in both supported engines."""
import unittest
from app.extensions import db
from app.models import User, GatewayNodeRole, GatewayMembership, NeoNode
from tests.browser import test_mobile_drawer as fixtures


class W1AuthorityBrowserTest(unittest.TestCase):
    def test_revision_session_csrf_permission_and_revoked_node(self):
        fixture = fixtures.MobileDrawerBrowserTest
        for engine in ('chromium', 'webkit'):
            with self.subTest(engine=engine):
                fixture.setUpClass()
                browser = None
                try:
                    browser = getattr(fixture.pw, engine).launch()
                    context = browser.new_context(viewport={'width': 390, 'height': 844})
                    page = context.new_page()
                    fixture().login(page, username='drawer-watcher')
                    fixture().ready(page, '/neoscorpion/fueler')
                    for route in ('fuel-assignments/revision', 'fuel-dispatch/revision'):
                        result = page.evaluate('''async route => {
                            const r = await fetch('/neoscorpion/' + route);
                            return {status: r.status, cache: r.headers.get('Cache-Control'), body: await r.json()};
                        }''', route)
                        self.assertEqual(result['status'], 200)
                        self.assertEqual(result['cache'], 'no-store')
                        self.assertFalse(result['body']['current_operation'])
                        self.assertIsNone(result['body']['operation_id'])
                        self.assertEqual(result['body']['revision'], 0)
                    # A read grant and valid CSRF do not grant Dispatcher mutation authority.
                    status = page.evaluate('''async () => {
                        const token = document.querySelector('meta[name="csrf-token"]').content;
                        return (await fetch('/neoscorpion/fuel-dispatch/assignment', {
                            method: 'POST', headers: {'X-CSRFToken': token},
                            body: new URLSearchParams({assignment_id: '99999'})
                        })).status;
                    }''')
                    self.assertEqual(status, 403)
                    with fixture.app.app_context():
                        user = User.query.filter_by(username='drawer-watcher').one()
                        membership = GatewayMembership.query.filter_by(user_id=user.id).one()
                        node = NeoNode.query.filter_by(code='scorpion').one()
                        grant = GatewayNodeRole.query.filter_by(
                            gateway_membership_id=membership.id, node_id=node.id).one()
                        grant_id = grant.id
                        grant.is_active = False
                        db.session.commit()
                    rejected = page.evaluate('''async () => {
                        const r = await fetch('/neoscorpion/fuel-assignments/revision', {redirect:'manual'});
                        return r.type === 'opaqueredirect' || r.status === 403;
                    }''')
                    self.assertTrue(rejected)
                    with fixture.app.app_context():
                        self.assertFalse(db.session.get(GatewayNodeRole, grant_id).is_active)
                    page.screenshot(path=str(fixture.evidence / f'w1-authority-{engine}.png'))
                    context.close()
                finally:
                    if browser:
                        browser.close()
                    fixture.tearDownClass()
