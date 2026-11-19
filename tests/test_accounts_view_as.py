from datetime import datetime
from unittest import TestCase, skipUnless
from unittest.mock import patch

from flask import g
from sqlalchemy import event, text

from app.extensions import db
from app.models import FuelDriverIdentity, Gateway, NeoScorpionVendorAccount, NeoScorpionVendorNight
from app.services.neoscorpion_assets import set_nightly_fueler_break
from tests import test_account_foundation_postgres as postgres
from tests import test_neoscorpion_vendor_workflow as vendor_fixtures
from tests.test_account_security_phase1 import fixture_user, PASSWORD


VIEW_AS = '/accounts/view-as'


def supervisor_fixture(gateway, actor):
    user = fixture_user('preview-supervisor')
    db.session.add_all([
        NeoScorpionVendorAccount(user_id=user.id, gateway_id=gateway.id, enabled=True,
                                 invited_by_user_id=actor.id),
        FuelDriverIdentity(user_id=user.id, requested_driver=True, account_type='vendor_supervisor',
                           verified_by_user_id=actor.id, verified_at=datetime.utcnow(),
                           verified_ups_id=user.employee_id),
    ])
    return user


class AccountsViewAsTest(TestCase):
    def setUp(self):
        fixture = self.fixture = vendor_fixtures.VendorWorkflowTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.prepare()
        self.admin = fixture_user('preview-owner', platform='grandmaster')
        self.fueler = fixture.fueler
        self.fueler.email_verified_at = datetime.utcnow()
        self.other_fueler = fixture_user('other-fueler', nodes={'scorpion': 'operator'})
        other_mission = fixture.fixture._mission('UPS876', 'N876UP', 25000, 18)
        fixture.fixture._assignment(other_mission).assigned_fueler_user_id = self.other_fueler.id
        self.driver, self.driver_account = fixture.connect()
        self.driver.employee_id = '1234567'
        db.session.add(FuelDriverIdentity(user_id=self.driver.id, requested_driver=True,
            account_type='fuel_truck_driver', verified_by_user_id=self.admin.id,
            verified_at=datetime.utcnow(), verified_ups_id=self.driver.employee_id))
        self.supervisor = supervisor_fixture(fixture.gateway, self.admin)
        self.client = fixture.client
        self.login(self.admin, PASSWORD)

    def login(self, user, password):
        db.session.commit()
        g.pop('_login_user', None)
        self.client.post('/logout')
        g.pop('_login_user', None)
        self.assertEqual(self.client.post('/login', data={'email': user.email, 'password': password}).status_code, 302)

    def get(self, key, user=None, part=None):
        subject = {'user': user.id} if user else {}
        url = f'{VIEW_AS}/{key}' + (f'/live/{part}' if part else '')
        return self.client.get(url, query_string=subject)

    def test_preview_contains_real_scoped_assignments_and_current_access_without_writes(self):
        statements = []

        def collect(_connection, _cursor, statement, *_args):
            if statement.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')):
                statements.append(statement)

        with self.client.session_transaction() as session:
            before = dict(session)
        engine = db.engine
        event.listen(engine, 'before_cursor_execute', collect)
        try:
            index = self.client.get(VIEW_AS)
            self.assertEqual(index.status_code, 200)
            self.assertIn(self.supervisor.display_name.encode(), index.data)
            for key, user in [('fuel-truck-driver', self.driver), ('vendor-supervisor', self.supervisor),
                              ('fueler', self.fueler)]:
                with self.subTest(key=key):
                    page = self.get(key, user)
                    self.assertEqual(page.status_code, 200, page.data[:300])
                    self.assertIn(b'PREVIEW MODE', page.data)
                    self.assertIn(b'N491UP', page.data)
                    self.assertNotIn(b'<form', page.data)
                    self.assertEqual(page.headers['Cache-Control'], 'private, no-store')
                    self.assertTrue(any("form-action 'none'" in policy for policy in
                                        page.headers.getlist('Content-Security-Policy')))
                    self.assertEqual(self.get(key, user, 'revision').status_code, 200)
                    if key == 'fueler':
                        self.assertNotIn(b'N876UP', page.data)
                        panel = self.get(key, user, 'panel')
                        self.assertEqual(panel.status_code, 200)
                        self.assertIn(b'N491UP', panel.data)
                        self.assertNotIn(b'N876UP', panel.data)
                    if key == 'vendor-supervisor':
                        self.assertIsNone(db.session.get(NeoScorpionVendorNight,
                            (self.fixture.operation.id, self.supervisor.id)))
            self.assertIn(b'N876UP', self.get('fueler', self.other_fueler).data)
            db.session.flush()
        finally:
            event.remove(engine, 'before_cursor_execute', collect)
        self.assertEqual(statements, [])
        with self.client.session_transaction() as session:
            self.assertEqual(session['_user_id'], before['_user_id'])

    def test_role_baselines_sort_rollover_and_live_fueler_status(self):
        for key, label in [('fuel-truck-driver', b'AWAITING DISPATCHER APPROVAL'),
                           ('vendor-supervisor', b'SUPERVISOR VIEW'),
                           ('fueler', b'No fuel assignments')]:
            page = self.get(key)
            self.assertEqual(page.status_code, 200, page.data[:300])
            self.assertIn(label, page.data)
            self.assertEqual(self.get(key, part='revision').status_code, 200)
        set_nightly_fueler_break(self.fixture.operation, self.fueler, '1', '0')
        db.session.commit()
        self.assertTrue(self.get('fueler', self.fueler, 'revision').json['fueler_on_break'])
        self.assertFalse(self.get('fueler', self.other_fueler, 'revision').json['fueler_on_break'])
        with patch('app.services.neoscorpion.current_sort_operation', return_value=None):
            self.assertIn(b'NO ACTIVE SORT', self.get('vendor-supervisor', self.supervisor).data)
            self.assertIn(b'NO ACTIVE SORT', self.get('fuel-truck-driver', self.driver).data)
            self.assertIsNone(self.get('fueler', self.fueler, 'revision').json['operation_id'])

    def test_role_gateway_and_revocation_boundaries(self):
        self.assertEqual(self.get('vendor-supervisor', self.driver, 'revision').status_code, 403)
        self.assertEqual(self.get('fuel-truck-driver', self.supervisor).status_code, 403)
        self.assertEqual(self.get('fueler', self.driver, 'panel').status_code, 403)
        self.assertEqual(self.get('deicer').status_code, 404)
        self.assertEqual(self.get('fuel-truck-driver', self.driver, 'panel').status_code, 404)
        other = Gateway(code='ALT', name='Foreign fixture', is_active=True)
        db.session.add(other)
        db.session.flush()
        self.driver_account.gateway_id = other.id
        db.session.commit()
        self.assertEqual(self.get('fuel-truck-driver', self.driver, 'revision').status_code, 403)
        self.fueler.is_active = False
        db.session.commit()
        self.assertEqual(self.get('fueler', self.fueler, 'panel').status_code, 403)
        self.admin.role = 'master'
        db.session.commit()
        self.assertEqual(self.get('vendor-supervisor', self.supervisor, 'revision').status_code, 403)
        self.login(self.other_fueler, PASSWORD)
        self.assertEqual(self.client.get(VIEW_AS).status_code, 403)
        self.assertEqual(self.get('fueler', self.other_fueler, 'panel').status_code, 403)

    def test_preview_cannot_post_to_preview_or_production_routes(self):
        for path, headers in [
            (VIEW_AS, {}), (VIEW_AS + '/fueler/live/panel', {}),
            ('/logout', {'Referer': 'http://localhost' + VIEW_AS + '/fueler'}),
            ('/neoscorpion/fuel-dispatch/assignment', {'Referer': 'http://localhost' + VIEW_AS + '/fueler'}),
        ]:
            with self.subTest(path=path):
                response = self.client.post(path, headers=headers)
                self.assertEqual(response.status_code, 403)
                self.assertIn('read only', response.json['error'])
        self.assertEqual(self.client.get(VIEW_AS).status_code, 200)


@skipUnless(postgres.URI, 'Requires disposable loopback PostgreSQL')
class AccountsViewAsPostgresTest(TestCase):
    setUpClass = classmethod(postgres.AccountFoundationPostgresTest.setUpClass.__func__)
    tearDownClass = classmethod(postgres.AccountFoundationPostgresTest.tearDownClass.__func__)
    prepare = postgres.AccountFoundationPostgresTest.prepare

    def test_admin_session_uses_read_only_postgres_transaction_for_live_projection(self):
        from app.auth.view_as import supervisor_gateway_context
        from app.models import User

        actor_id, _ = self.prepare()
        with self.app.app_context():
            email = db.session.get(User, actor_id).email
        client = self.app.test_client()
        self.assertEqual(client.post('/login', data={'email': email, 'password': PASSWORD}).status_code, 302)

        def checked_context(gateway):
            self.assertEqual(db.session.execute(text('SHOW transaction_read_only')).scalar(), 'on')
            return supervisor_gateway_context(gateway)

        with patch('app.auth.view_as.supervisor_gateway_context', side_effect=checked_context) as projection:
            self.assertEqual(client.get(VIEW_AS + '/vendor-supervisor').status_code, 200)
            revision = client.get(VIEW_AS + '/vendor-supervisor/live/revision')
            self.assertEqual(revision.status_code, 200)
            self.assertIn('supervisor_revision', revision.json)
            self.assertEqual(projection.call_count, 2)
        with self.app.app_context():
            self.assertEqual(db.session.execute(text('SHOW transaction_read_only')).scalar(), 'off')
