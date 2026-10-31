"""Revocation is authoritative across requests, projection paths and provisioning."""
from datetime import datetime
import unittest
from unittest.mock import patch

from flask import g
from flask_login import current_user
from sqlalchemy import event

from app import create_app
from app.extensions import db
from app.auth.decorators import gateway_node_required
from app.models import Gateway, GatewayMembership, GatewayNodeRole, NeoNode, PortalAppAccess, User
from app.services import access_control as access
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class AuthorizationRevocationTest(unittest.TestCase):
    def setUp(self):
        config = type('Config', (), dict(SECRET_KEY='revocation-test', TESTING=True,
            AUTO_BOOTSTRAP_DATABASE=False, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            SQLALCHEMY_TRACK_MODIFICATIONS=False))
        self.app = create_app(config)
        for node, _, _ in access.DEFAULT_NEONODES:
            def probe(node=node):
                return {'role': access.get_user_node_role(current_user, 'RFD', node)}
            self.app.add_url_rule('/_revocation/'+node, 'revocation_'+node,
                gateway_node_required(node)(probe), methods=['GET','POST','PUT','PATCH','DELETE'])
        self.ctx = self.app.app_context(); self.ctx.push()
        db.create_all(); ensure_default_permission_rules()
        self.user = User(username='revocation-user', role='master', email='revoke@example.test',
                         email_verified_at=datetime(2026, 1, 1))
        set_user_password(self.user, 'TestPassword123!')
        db.session.add(self.user); db.session.flush()
        self.membership = access.backfill_default_gateway_node_roles(self.user, 'master')
        db.session.commit()
        self.gateway = self.membership.gateway
        self.client = self.app.test_client()
        self.login(self.client)

    def tearDown(self):
        db.session.remove(); db.drop_all(); db.engine.dispose(); self.ctx.pop()

    def login(self, client):
        response = client.post('/login', data={'username':self.user.username,'password':'TestPassword123!'})
        self.assertEqual(response.status_code, 302)
        g.pop('_login_user', None)

    def grant(self, code='neogateway'):
        return PortalAppAccess.query.filter_by(user_id=self.user.id, app_code=code).one()

    def test_access_request_state_matrix_and_preserved_metadata(self):
        for code in access.PORTAL_APP_CODES:
            grant = access.ensure_user_app_access(self.user, code)
            for status, active in [('approved',True),('approved',False),('pending',True),('pending',False),('denied',True),('denied',False)]:
                with self.subTest(code=code,status=status,active=active):
                    grant.status=status;grant.is_active=active
                    grant.approved_at=datetime(2026,1,1);grant.approved_by_user_id=self.user.id
                    grant.approval_notes='Original approval';grant.denial_notes='Original denial'
                    db.session.commit()
                    result = access.request_app_access_for_user(self.user,code); db.session.commit()
                    expected = 'pending' if status=='approved' and not active else status
                    self.assertEqual(result.status, expected)
                    self.assertEqual(access.user_has_app_access(self.user,code),status=='approved' and active)
                    self.assertEqual(result.approved_at,datetime(2026,1,1))
                    self.assertEqual(result.approved_by_user_id,self.user.id)
                    self.assertTrue(result.approval_notes.startswith('Original approval'))
                    self.assertEqual(result.denial_notes,'Original denial')
                    if status=='denied': self.assertEqual(result.is_active, active)
                    notes=result.approval_notes
                    access.request_app_access_for_user(self.user,code);db.session.commit()
                    self.assertEqual(result.approval_notes,notes)

    def test_disabled_membership_reinstatement_enters_queue_without_authority(self):
        self.membership.is_active=False;self.membership.approval_notes='Previous decision'
        self.membership.approved_at=datetime(2026,1,1);db.session.commit()
        self.client.post('/portal/request-access',data={'app_code':'neogateway'})
        self.assertEqual(self.membership.status,'pending')
        self.assertFalse(access.user_has_gateway_access(self.user,'RFD'))
        self.assertEqual(self.membership.approved_at,datetime(2026,1,1))
        self.assertIn('Reinstatement requested',self.membership.approval_notes)
        from app.auth.routes import _pending_memberships_for_gateway
        self.assertIn(self.membership,_pending_memberships_for_gateway(self.gateway).all())

    def test_disabled_apps_enter_existing_administrator_queue(self):
        from app.auth.routes import _portal_app_access_requests
        for code in access.PORTAL_APP_CODES:
            grant=access.ensure_user_app_access(self.user,code)
            grant.status='approved';grant.is_active=False;db.session.commit()
            response=self.client.post('/portal/request-access',data={'app_code':code})
            self.assertEqual(response.status_code,302)
            self.assertFalse(access.user_has_app_access(self.user,code))
            self.assertIn(grant,_portal_app_access_requests(app_code=code,status='pending').all())

    def test_node_revocations_deny_regular_lightweight_and_batched_resolution(self):
        for node in NeoNode.query.all():
            role=GatewayNodeRole.query.filter_by(gateway_membership_id=self.membership.id,node_id=node.id).one()
            for prior in ('watcher','master'):
                role.role=prior;role.is_active=False;db.session.commit()
                for path in ('regular','lightweight','batch'):
                    with self.subTest(node=node.code,prior=prior,path=path),self.app.test_request_context('/_probe'):
                        if path=='lightweight':
                            g.is_lightweight_live_state_request=True
                            access.prime_lightweight_live_request_scope(self.user,node.code)
                        if path=='batch':access.prime_user_node_roles_for_request(self.user,'RFD',(node.code,))
                        self.assertIsNone(access.get_user_node_role(self.user,'RFD',node.code))
                access.seed_gateway_node_roles(self.membership,'grandmaster')
                db.session.commit()
                self.assertEqual((role.role,role.is_active),(prior,False))

    def test_direct_mutations_and_sessions_cannot_restore_revoked_nodes(self):
        second=self.app.test_client();self.login(second)
        for node in NeoNode.query.all():
            role=GatewayNodeRole.query.filter_by(gateway_membership_id=self.membership.id,node_id=node.id).one()
            role.is_active=False;db.session.commit()
            for client in (self.client,second):
                for method in ('GET','POST','PUT','PATCH','DELETE'):
                    g.pop('_login_user',None)
                    response=client.open('/_revocation/'+node.code,method=method)
                    self.assertEqual(response.status_code,302)
                    self.assertNotEqual(response.location,'/login')
            db.session.refresh(role);self.assertFalse(role.is_active)
        self.client.post('/logout');g.pop('_login_user',None);self.login(self.client)
        self.assertEqual(self.client.get('/_revocation/rain').status_code,302)

    def test_real_node_pages_reject_revoked_roles(self):
        paths={'motherbrain':'/motherbrain','sektor':'/neosektor','ermac':'/neoermac',
               'rain':'/neorain','scorpion':'/neoscorpion','subzero':'/neosubzero/'}
        for code,path in paths.items():
            node=NeoNode.query.filter_by(code=code).one()
            role=GatewayNodeRole.query.filter_by(gateway_membership_id=self.membership.id,node_id=node.id).one()
            role.is_active=False;db.session.commit();g.pop('_login_user',None)
            response=self.client.get(path)
            self.assertIn(response.status_code,(302,403))
            db.session.refresh(role);self.assertFalse(role.is_active)

    def test_active_roles_and_grandmaster_survive_provisioning_unchanged(self):
        roles=GatewayNodeRole.query.filter_by(gateway_membership_id=self.membership.id).all()
        for index,role in enumerate(roles):role.role=('watcher','master','grandmaster')[index%3]
        db.session.commit();expected={r.id:r.role for r in roles}
        access.backfill_default_gateway_node_roles(self.user,'grandmaster');db.session.commit()
        self.assertEqual({r.id:r.role for r in roles},expected)
        for role in roles:self.assertEqual(access.get_user_node_role(self.user,'RFD',role.node.code),expected[role.id])
        self.assertEqual(self.grant().role,'master')

    def test_global_disable_survives_initialization_requests_and_legacy_bootstrap(self):
        node=NeoNode.query.filter_by(code='rain').one()
        self.gateway.is_active=False;node.is_active=False;db.session.commit()
        access.ensure_default_gateway_and_nodes()
        access.request_default_gateway_access_for_user(self.user)
        access.backfill_default_gateway_node_roles(self.user,'grandmaster');db.session.commit()
        self.assertFalse(self.gateway.is_active);self.assertFalse(node.is_active)
        self.assertFalse(access.user_has_gateway_access(self.user,'RFD'))
        with self.app.test_request_context('/_probe'):
            access.prime_lightweight_live_request_scope(self.user,'rain')
            self.assertIsNone(access.get_user_node_role(self.user,'RFD','rain'))
        self.gateway.is_active=True;db.session.commit()
        with self.app.test_request_context('/_probe'):
            access.ensure_default_gateway_and_nodes()
            self.assertIsNone(access.get_user_node_role(self.user,'RFD','rain'))

    def test_legacy_bootstrap_cannot_restore_revoked_grants(self):
        role=GatewayNodeRole.query.first();role.is_active=False;role.role='watcher'
        self.membership.is_active=False;self.grant().is_active=False;db.session.commit()
        access.backfill_default_gateway_node_roles(self.user,'grandmaster');db.session.commit()
        self.assertFalse(self.membership.is_active);self.assertFalse(self.grant().is_active)
        self.assertEqual((role.role,role.is_active),('watcher',False))

    def test_missing_initial_grants_still_provision_and_cache_clears(self):
        missing=User(username='new-initial',role='watcher');set_user_password(missing,'TestPassword123!');db.session.add(missing);db.session.flush()
        membership=access.request_default_gateway_access_for_user(missing);db.session.commit()
        self.assertFalse(access.user_has_gateway_access(missing,'RFD'))
        membership.status='approved';grant=access.ensure_user_app_access(missing,'neogateway')
        grant.status='approved';db.session.commit()
        self.assertEqual(access.get_user_node_role(missing,'RFD','rain'),'watcher')
        with self.app.test_request_context('/_probe'):
            self.assertEqual(access.get_user_node_role(self.user,'RFD','rain'),'master')
            role=GatewayNodeRole.query.join(NeoNode).filter(NeoNode.code=='rain',GatewayNodeRole.gateway_membership_id==self.membership.id).one()
            role.is_active=False;db.session.commit()
            self.assertIsNone(access.get_user_node_role(self.user,'RFD','rain'))

    def test_repeated_permission_checks_do_not_write(self):
        statements=[]
        def capture(_conn,_cursor,statement,*_args):statements.append(statement)
        event.listen(db.engine,'before_cursor_execute',capture)
        try:
            for _ in range(2):
                with self.app.test_request_context('/_probe'):
                    access.prime_user_node_roles_for_request(self.user,'RFD',('rain','sektor','ermac'))
                    for code in ('rain','sektor','ermac'):self.assertEqual(access.get_user_node_role(self.user,'RFD',code),'master')
        finally:event.remove(db.engine,'before_cursor_execute',capture)
        self.assertFalse([s for s in statements if s.lstrip().upper().startswith(('INSERT','UPDATE','DELETE'))])

    def test_administrator_approval_can_deliberately_restore_access(self):
        self.grant().is_active=False;db.session.commit()
        access.request_app_access_for_user(self.user,'neogateway');db.session.commit()
        from app.auth.routes import _approve_portal_app_access
        from flask_login import login_user
        with self.app.test_request_context('/_admin'):
            login_user(self.user)
            with patch('app.auth.routes.email_service.send_access_approved',return_value={'sent':False}):
                _approve_portal_app_access(self.grant(),'master','Explicit reinstatement')
            db.session.commit()
        self.assertTrue(access.user_has_app_access(self.user,'neogateway'))
        self.assertTrue(access.user_has_gateway_access(self.user,'RFD'))

    def test_actual_live_revision_endpoints_respect_node_revocation(self):
        paths={'sektor':'/neosektor/driver-routing/state','ermac':'/neoermac/door-view/state',
               'rain':'/neorain/inbound/revision','subzero':'/neosubzero/outbound/revision'}
        for code,path in paths.items():
            role=GatewayNodeRole.query.join(NeoNode).filter(NeoNode.code==code,GatewayNodeRole.gateway_membership_id==self.membership.id).one()
            role.is_active=False;db.session.commit();g.pop('_login_user',None)
            self.assertIn(self.client.get(path).status_code,(302,403))
            db.session.refresh(role);self.assertFalse(role.is_active)

    def test_registration_preserves_disabled_gateway_and_nodes(self):
        self.gateway.is_active=False;node=NeoNode.query.filter_by(code='rain').one()
        node.is_active=False;db.session.commit()
        from tests.test_auth_account_flows import AuthAccountFlowsTest
        data=AuthAccountFlowsTest._account_form(self,email='new-registration@example.test',employee_id='NEW-REG')
        with patch('app.auth.routes.email_service.send_email_verification',return_value={'sent':False}):
            response=self.app.test_client().post('/create-account',data=data)
        self.assertEqual(response.status_code,200)
        db.session.refresh(self.gateway);db.session.refresh(node)
        self.assertFalse(self.gateway.is_active);self.assertFalse(node.is_active)

    def test_admin_form_keeps_disabled_node_and_unrelated_disabled_app(self):
        from flask_login import login_user
        from app.auth.routes import _node_role_rows,_apply_node_role_form,_apply_gateway_membership_edit_form
        role=GatewayNodeRole.query.join(NeoNode).filter(NeoNode.code=='rain',GatewayNodeRole.gateway_membership_id==self.membership.id).one()
        role.role='watcher';role.is_active=False;self.grant().is_active=False;db.session.commit()
        with self.app.test_request_context('/_admin',method='POST',data={f'node_{role.node_id}':'disabled',
                'membership_status':'approved','membership_is_active':'1'}):
            login_user(self.user)
            row=next(row for row in _node_role_rows(self.user,self.membership) if row['node'].code=='rain')
            self.assertEqual(row['effective_role'],'disabled')
            _apply_gateway_membership_edit_form(self.user,self.gateway,self.membership)
            _apply_node_role_form(self.user,self.membership);db.session.commit()
        self.assertFalse(role.is_active);self.assertFalse(self.grant().is_active)

    def test_admin_role_edit_deliberately_restores_revoked_node(self):
        self.user.role='grandmaster';db.session.commit()
        from flask_login import login_user
        from app.auth.routes import _apply_node_role_form
        role=GatewayNodeRole.query.join(NeoNode).filter(NeoNode.code=='rain',GatewayNodeRole.gateway_membership_id==self.membership.id).one()
        role.role='watcher';role.is_active=False;db.session.commit()
        with self.app.test_request_context('/_admin',method='POST',data={f'node_{role.node_id}':'watcher'}):
            login_user(self.user)
            _apply_node_role_form(self.user,self.membership);db.session.commit()
        self.assertTrue(role.is_active);self.assertEqual(role.role,'watcher')

    def test_lower_admin_cannot_restore_same_revoked_higher_role(self):
        from flask_login import login_user
        from app.auth.routes import _apply_node_role_form
        role=GatewayNodeRole.query.join(NeoNode).filter(NeoNode.code=='rain',GatewayNodeRole.gateway_membership_id==self.membership.id).one()
        role.role='grandmaster';role.is_active=False;db.session.commit()
        with self.app.test_request_context('/_admin',method='POST',data={f'node_{role.node_id}':'grandmaster'}):
            login_user(self.user)
            with self.assertRaises(ValueError):_apply_node_role_form(self.user,self.membership)
            db.session.rollback()
        self.assertFalse(role.is_active)

    def test_authenticated_admin_forms_show_disabled_and_require_deliberate_selection(self):
        from tests.html_contracts import document
        self.user.role='grandmaster';db.session.commit()
        role=GatewayNodeRole.query.join(NeoNode).filter(NeoNode.code=='rain',GatewayNodeRole.gateway_membership_id==self.membership.id).one()
        role.role='watcher';role.is_active=False;db.session.commit();g.pop('_login_user',None)
        for suffix in ('/edit','/roles',''):
            response=self.client.get(f'/portal/manage/users/{self.user.id}'+suffix)
            self.assertEqual(response.status_code,200)
            self.assertIn(b'Disabled',response.data)
            if suffix:
                select=document(response).one('select',name=f'node_{role.node_id}')
                self.assertIn('selected',select.one('option',value='disabled').attrs)
        response=self.client.post(f'/portal/manage/users/{self.user.id}/roles',data={f'node_{role.node_id}':'disabled'})
        self.assertEqual(response.status_code,302);db.session.refresh(role);self.assertFalse(role.is_active)
        response=self.client.post(f'/portal/manage/users/{self.user.id}/roles',data={f'node_{role.node_id}':'watcher'})
        self.assertEqual(response.status_code,302);db.session.refresh(role);self.assertTrue(role.is_active)

    def test_nonadministrator_cannot_use_role_restoration_endpoint(self):
        role=GatewayNodeRole.query.join(NeoNode).filter(NeoNode.code=='rain',GatewayNodeRole.gateway_membership_id==self.membership.id).one()
        role.is_active=False;db.session.commit();g.pop('_login_user',None)
        response=self.client.post(f'/portal/manage/users/{self.user.id}/roles',data={f'node_{role.node_id}':'master'})
        self.assertIn(response.status_code,(302,403));db.session.refresh(role);self.assertFalse(role.is_active)

    def test_lower_admin_cannot_disable_higher_active_role(self):
        from flask_login import login_user
        from app.auth.routes import _apply_node_role_form
        role=GatewayNodeRole.query.join(NeoNode).filter(NeoNode.code=='rain',GatewayNodeRole.gateway_membership_id==self.membership.id).one()
        role.role='grandmaster';db.session.commit()
        with self.app.test_request_context('/_admin',method='POST',data={f'node_{role.node_id}':'disabled'}):
            login_user(self.user)
            with self.assertRaises(ValueError):_apply_node_role_form(self.user,self.membership)
            db.session.rollback()
        self.assertTrue(role.is_active)

    def test_self_service_ignores_forged_role_and_gateway_fields(self):
        grant=access.ensure_user_app_access(self.user,'neostaffing')
        grant.status='approved';grant.role='watcher';grant.is_active=False;db.session.commit()
        response=self.client.post('/portal/request-access',data={'app_code':'neostaffing','role':'grandmaster',
            'gateway_id':'999999','node_id':'999999','status':'approved','is_active':'1'})
        self.assertEqual(response.status_code,302);db.session.refresh(grant)
        self.assertEqual((grant.status,grant.role),('pending','watcher'))
        self.assertFalse(access.user_has_app_access(self.user,'neostaffing'))
        total=PortalAppAccess.query.count()
        self.client.post('/portal/request-access',data={'app_code':'unregistered','role':'grandmaster'})
        self.assertEqual(PortalAppAccess.query.count(),total)

    def test_neostaffing_disabled_access_blocks_direct_page_and_background_permission(self):
        from app.services.permission_rules import user_can
        grant=access.ensure_user_app_access(self.user,'neostaffing')
        grant.status='approved';grant.role='master';grant.is_active=False;db.session.commit()
        g.pop('_login_user',None)
        self.assertIn(self.client.get('/neostaffing/people').status_code,(302,403))
        self.assertFalse(user_can('neostaffing.people.edit',self.user))
        access.request_app_access_for_user(self.user,'neostaffing');db.session.commit()
        self.assertFalse(user_can('neostaffing.people.edit',self.user))

    def test_new_grant_savepoint_obeys_caller_rollback(self):
        self.assertIsNone(PortalAppAccess.query.filter_by(user_id=self.user.id,app_code='neostaffing').first())
        access.ensure_user_app_access(self.user,'neostaffing')
        db.session.rollback()
        self.assertIsNone(PortalAppAccess.query.filter_by(user_id=self.user.id,app_code='neostaffing').first())

    def test_new_default_gateway_and_nodes_obey_caller_rollback(self):
        GatewayNodeRole.query.delete();GatewayMembership.query.delete();PortalAppAccess.query.delete()
        NeoNode.query.delete();Gateway.query.delete();db.session.commit()
        access.ensure_default_gateway_and_nodes()
        db.session.rollback()
        self.assertEqual(Gateway.query.count(),0)
        self.assertEqual(NeoNode.query.count(),0)
