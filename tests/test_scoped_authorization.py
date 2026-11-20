"""Phase 5: one engine, reviewed atomic cutover, exact scopes and rollback."""
from unittest import TestCase
from unittest.mock import patch
from flask_login import login_user,logout_user
from sqlalchemy import event

from app import create_app
from app.extensions import db
from app.models import (User,Gateway,NeoNode,GatewayMembership,GatewayNodeRole,PortalAppAccess,
    AccountAuthorizationState,AccountSecurityEvent,AccountScopedRole,AccountScopedPolicy,PermissionRule)
from app.services.access_control import ensure_default_gateway_and_nodes,get_user_node_role,prime_user_node_roles_for_request
from app.services.permission_rules import ensure_default_permission_rules,user_can
from app.services.account_authority import Scope,read_authority
from app.services.account_scope_cutover import review,activate,rollback_review,rollback,inventory,digest
from app.services.account_scope_runtime import scoped_active,can_administer,owner_scope
from app.services import scoped_access_management as management
from app.services.accounts_workspace import review_token
from app.services.account_foundation import AccountConflict,stage_role,FoundationNotActivated
from app.services.account_foundation_schema import ensure_account_history_guards
from app.services.request_cache import clear_request_cache
from tests.test_account_security_phase1 import fixture_user,PASSWORD


def prepare_scope_fixture():
    ensure_default_gateway_and_nodes();ensure_default_permission_rules()
    codes=[n.code for n in NeoNode.query.all()]
    root=fixture_user('scope-root',platform='grandmaster',nodes={c:'grandmaster' for c in codes})
    backup=fixture_user('scope-backup',platform='grandmaster',nodes={c:'grandmaster' for c in codes})
    target=fixture_user('scope-subject',nodes={'scorpion':'operator'})
    operator=fixture_user('scope-master',nodes={'scorpion':'master'})
    inherited=fixture_user('scope-inherited',nodes={'scorpion':'master','subzero':'master'})
    app=PortalAppAccess.query.filter_by(user_id=inherited.id,app_code='neogateway').one();app.role='simulator'
    member=GatewayMembership.query.filter_by(user_id=inherited.id).one()
    for row in GatewayNodeRole.query.filter_by(gateway_membership_id=member.id).all():
        if row.node.code not in {'scorpion','subzero'}:db.session.delete(row)
    # Match legitimate production overrides rather than obsolete default GM labels.
    for key in ('neomotherbrain.sort_timeline.view','neomotherbrain.sort_timeline.edit'):
        PermissionRule.query.filter_by(permission_key=key).one().minimum_role='simulator'
    PermissionRule.query.filter_by(permission_key='neomotherbrain.flight_api_auto_poll.trigger').one().minimum_role='grandmaster'
    db.session.commit()
    return root,backup,target,operator,inherited


class ScopedAuthorizationTest(TestCase):
    def setUp(self):
        self.app=create_app(type('Config',(),dict(SECRET_KEY='scope-tests',TESTING=True,AUTO_BOOTSTRAP_DATABASE=False,
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',SQLALCHEMY_TRACK_MODIFICATIONS=False)))
        self.ctx=self.app.test_request_context();self.ctx.push();db.create_all()
        ensure_account_history_guards(db.session.connection())
        self.root,self.backup,self.target,self.master,self.inherited=prepare_scope_fixture()
        self.gateway=Gateway.query.filter_by(code='RFD').one()
        self.node=NeoNode.query.filter_by(code='scorpion').one()
        self.scope=Scope('node','neogateway',self.gateway.id,self.node.id)
        self.as_user(self.root)

    def tearDown(self):
        db.session.rollback();logout_user();db.session.remove();db.drop_all();self.ctx.pop()

    def as_user(self,user):
        login_user(user);clear_request_cache()

    def cutover(self):
        self.as_user(self.root);result=review();self.assertEqual(result['blockers'],[],result)
        event_id=activate(result['token'],PASSWORD,'ACTIVATE SCOPED PERMISSIONS');db.session.commit();clear_request_cache()
        return event_id

    def set_role(self,role,user=None):
        target=user or self.target
        management.set_role(target.id,self.scope,role,review_token(target),'Verified scoped test');db.session.commit()

    def test_complete_engine_matrix_preserves_all_existing_decisions(self):
        users=User.query.order_by(User.id).all();keys=[r.permission_key for r in PermissionRule.query.all()]
        before={}
        for user in users:
            self.as_user(user)
            prime_user_node_roles_for_request(user,'RFD',[n.code for n in NeoNode.query.all()])
            before.update({(user.id,key):user_can(key,user=user) for key in keys})
        receipt=self.cutover();after={}
        for user in users:
            self.as_user(user)
            after.update({(user.id,key):user_can(key,user=user) for key in keys})
        self.assertEqual(before,after)
        self.assertEqual(GatewayNodeRole.query.filter_by(role='grandmaster').count(),0)
        audit=db.session.get(AccountSecurityEvent,receipt)
        self.assertEqual(audit.details['compared_decisions'],len(before))
        self.assertEqual(len(audit.details['inherited']),5)
        self.assertEqual(User.query.filter_by(role='grandmaster').count(),2)
        for code in ('motherbrain','sektor','reptile','ermac','rain'):
            self.assertEqual(get_user_node_role(self.inherited,'RFD',code),'simulator')

    def test_cutover_rejects_unmapped_node_grandmaster_and_permission_differences(self):
        grant=management.existing_role(self.target,self.scope);grant.role='grandmaster';db.session.commit();clear_request_cache()
        result=review();self.assertTrue(result['blockers'])
        with self.assertRaises(ValueError):activate(result['token'],PASSWORD,'ACTIVATE SCOPED PERMISSIONS')
        self.assertIsNone(db.session.get(AccountAuthorizationState,1))
        self.assertEqual(grant.role,'grandmaster')

    def test_stale_inventory_bad_password_confirmation_and_duplicate_are_atomic(self):
        token=review()['token'];self.target.role='operator';db.session.commit()
        with self.assertRaises(AccountConflict):activate(token,PASSWORD,'ACTIVATE SCOPED PERMISSIONS')
        for password,confirmation in (('wrong','ACTIVATE SCOPED PERMISSIONS'),(PASSWORD,'yes')):
            with self.subTest(confirmation=confirmation),self.assertRaises(ValueError):activate(review()['token'],password,confirmation)
        self.assertEqual(AccountSecurityEvent.query.count(),0)
        self.cutover()
        with self.assertRaises(AccountConflict):activate(review()['token'],PASSWORD,'ACTIVATE SCOPED PERMISSIONS')
        self.assertEqual(AccountSecurityEvent.query.filter_by(action='scoped_authorization_activated').count(),1)

    def test_failed_cutover_rolls_back_every_role_and_receipt(self):
        token=review()['token'];before=digest(inventory())
        def fail(_mapper,_connection,_target):raise RuntimeError('Injected final switch failure')
        event.listen(AccountAuthorizationState,'before_insert',fail)
        try:
            with self.assertRaises(RuntimeError):activate(token,PASSWORD,'ACTIVATE SCOPED PERMISSIONS')
        finally:event.remove(AccountAuthorizationState,'before_insert',fail)
        self.assertEqual(digest(inventory()),before);self.assertEqual(AccountSecurityEvent.query.count(),0)
        self.assertFalse(scoped_active())

    def test_authenticated_rollback_preserves_history_and_rejects_duplicate(self):
        activation=self.cutover();reviewed=rollback_review();self.assertTrue(reviewed['available'])
        rollback(reviewed['token'],PASSWORD,'ROLL BACK SCOPED PERMISSIONS');db.session.commit();clear_request_cache()
        self.assertFalse(scoped_active());self.assertEqual(GatewayNodeRole.query.filter_by(role='grandmaster').count(),14)
        self.assertEqual(AccountScopedRole.query.filter_by(is_active=True).count(),0)
        self.assertEqual(AccountSecurityEvent.query.filter_by(action='scoped_authorization_rolled_back').count(),1)
        self.assertIsNotNone(db.session.get(AccountSecurityEvent,activation))
        with self.assertRaises(AccountConflict):rollback(reviewed['token'],PASSWORD,'ROLL BACK SCOPED PERMISSIONS')

    def test_rollback_never_overwrites_later_role_changes(self):
        self.cutover();token=rollback_review()['token'];self.set_role('simulator')
        self.assertFalse(rollback_review()['available'])
        with self.assertRaises(AccountConflict):rollback(token,PASSWORD,'ROLL BACK SCOPED PERMISSIONS')
        self.assertEqual(management.existing_role(self.target,self.scope).role,'simulator')

    def test_node_master_delegates_below_self_not_peers_self_or_other_scopes(self):
        self.cutover();self.as_user(self.master);self.set_role('simulator')
        self.assertEqual(management.existing_role(self.target,self.scope).role,'simulator')
        for role,target in (('master',self.target),('grandmaster',self.target),('operator',self.master),('disabled',self.inherited)):
            with self.subTest(role=role,target=target.id),self.assertRaises(ValueError):self.set_role(role,target)
        foreign=Scope('node','neogateway',self.gateway.id,NeoNode.query.filter_by(code='rain').one().id)
        with self.assertRaises(ValueError):management.set_role(self.target.id,foreign,'operator',review_token(self.target),'Wrong scope')
        self.assertEqual(self.target.role,'watcher')

    def test_revoked_node_does_not_return_from_app_or_prime_or_get(self):
        self.cutover();self.set_role('disabled');before=digest(inventory())
        for _ in range(2):
            clear_request_cache();prime_user_node_roles_for_request(self.target,'RFD',['scorpion'])
            self.assertIsNone(get_user_node_role(self.target,'RFD','scorpion'))
        self.assertEqual(digest(inventory()),before)
        new=NeoNode(code='future',name='Future',is_active=True);db.session.add(new);db.session.commit();clear_request_cache()
        self.assertIsNone(get_user_node_role(self.root,'RFD','future'))

    def test_stale_role_review_rejects_replay_after_session_rotation(self):
        self.cutover();token=review_token(self.target);epoch=self.target.auth_session_version
        self.set_role('simulator')
        self.assertEqual(self.target.auth_session_version,epoch+1)
        with self.assertRaises(AccountConflict):management.set_role(self.target.id,self.scope,'operator',token,'Stale review')
        self.assertEqual(AccountSecurityEvent.query.filter_by(action='scoped_role_changed').count(),1)

    def test_candidate_staging_and_direct_retired_grant_are_rejected(self):
        self.cutover()
        with self.assertRaises(FoundationNotActivated):stage_role(self.target.id,self.scope,'operator',True,'No staging',expected_version=0,expected_session=1,expected_target_session=1)
        from app.services.account_foundation import decide_access
        from app.services.account_authority import compare_authority
        with self.assertRaises(FoundationNotActivated):decide_access(1,'approved','Use live decisions',expected_version=1,expected_session=1,expected_target_session=1,expected_entitlement='invalid')
        self.assertEqual(compare_authority(self.target.id,[self.scope,Scope('neoapps')]),())
        row=management.existing_role(self.target,self.scope);row.role='grandmaster'
        with self.assertRaises(ValueError):db.session.flush()
        db.session.rollback();self.assertEqual(row.role,'operator')

    def test_local_policy_is_exact_scope_audited_stale_safe_and_has_reviewed_floor(self):
        self.cutover();self.as_user(self.master)
        rule=PermissionRule.query.filter_by(permission_key='neoscorpion.fuel_dispatch.edit').one()
        base=rule.minimum_role;token=management.policy_review(self.scope)
        management.set_policy(self.scope,token,[(rule.id,'master')]);db.session.commit();clear_request_cache()
        self.assertEqual(rule.minimum_role,base)
        override=AccountScopedPolicy.query.one();self.assertEqual(override.scope_key,self.scope.key)
        self.assertFalse(user_can(rule.permission_key,user=self.target))
        with self.assertRaises(AccountConflict):management.set_policy(self.scope,token,[(rule.id,'operator')])
        with self.assertRaises(ValueError):management.set_policy(self.scope,management.policy_review(self.scope),[(rule.id,'watcher')])
        foreign=PermissionRule.query.filter_by(permission_key='neoapps.user_management.edit').one()
        with self.assertRaises(ValueError):management.set_policy(self.scope,management.policy_review(self.scope),[(foreign.id,'watcher')])
        from app.services.account_scope_runtime import scoped_minimum
        other=Scope('node','neogateway',999,self.node.id)
        self.assertEqual(scoped_minimum(other,rule.permission_key,base),base)

    def test_application_admin_requires_platform_master_and_exact_app_grandmaster(self):
        self.cutover();scope=Scope('application','neostaffing')
        app=PortalAppAccess(user_id=self.master.id,app_code='neostaffing',status='approved',is_active=True,role='grandmaster')
        db.session.add(app);db.session.commit();clear_request_cache()
        self.assertFalse(can_administer(self.master,scope))
        self.master.role='master';db.session.commit();clear_request_cache()
        self.assertTrue(can_administer(self.master,scope))
        self.assertFalse(can_administer(self.master,Scope('application','neogateway')))
        pending=PortalAppAccess(user_id=self.target.id,app_code='neostaffing',status='pending',is_active=True,role='watcher')
        db.session.add(pending);db.session.commit();self.as_user(self.master)
        management.decide_application(self.target.id,scope,'operator','approved',review_token(self.target),'Staffing duties');db.session.commit()
        self.assertEqual(pending.status,'approved');self.assertEqual(self.target.role,'watcher')

    def test_protected_scope_commissioning_does_not_grant_platform_master(self):
        self.cutover();scope=Scope('gateway','neogateway',self.gateway.id)
        management.commission_scope(self.target.id,scope,review_token(self.target),PASSWORD,f'COMMISSION {self.target.id} {scope.key}','Gateway oversight');db.session.commit();clear_request_cache()
        self.assertEqual(self.target.role,'watcher');self.assertEqual(read_authority(self.target.id).role(scope),'grandmaster')
        self.assertFalse(can_administer(self.target,Scope('application','neogateway')))
        self.assertTrue(can_administer(self.target,self.scope))
        with self.assertRaises(AccountConflict):management.commission_scope(self.target.id,scope,review_token(self.target),PASSWORD,f'COMMISSION {self.target.id} {scope.key}','Duplicate oversight')

    def test_scope_role_guard_all_ranks_and_sensitive_hr(self):
        self.cutover()
        for rank in ('watcher','operator','simulator','master'):
            row=management.existing_role(self.master,self.scope);row.role=rank;db.session.commit();self.as_user(self.master)
            for selected in ('watcher','operator','simulator','master','grandmaster'):
                allowed=rank=='master' and selected in {'watcher','operator','simulator'}
                try:management.role_guard(self.master,self.target,self.scope,management.existing_role(self.target,self.scope),selected);actual=True
                except ValueError:actual=False
                self.assertEqual(actual,allowed,(rank,selected))
        self.assertEqual(owner_scope('neoapps.user_management.edit').kind,'neoapps')
        self.assertEqual(owner_scope('neoscorpion.refresh_settings.edit').kind,'gateway')

    def test_http_owned_pages_legacy_routes_and_gets_are_read_only(self):
        self.cutover();client=self.app.test_client()
        with client.session_transaction() as session:
            session['_user_id']=str(self.master.id);session['_fresh']=True;session['auth_session_version']=self.master.auth_session_version
        # A fresh request must not reuse the enclosing synthetic login proxy.
        from flask import g
        g.pop('_login_user',None);clear_request_cache();before=digest(inventory())
        for path in ('/neoscorpion/permissions','/neoscorpion/roles'):
            response=client.get(path);self.assertEqual(response.status_code,200,(path,response.status_code))
        self.assertEqual(client.get('/neorain/roles').status_code,403)
        self.assertEqual(client.get('/neostaffing/accounts').status_code,403)
        self.assertEqual(digest(inventory()),before)
        with client.session_transaction() as session:
            session['_user_id']=str(self.root.id);session['auth_session_version']=self.root.auth_session_version
        g.pop('_login_user',None);clear_request_cache()
        # Platform oversight does not require or grant an operational app entitlement.
        self.assertEqual(client.get('/neostaffing/permissions').status_code,200)
        self.assertIsNone(PortalAppAccess.query.filter_by(user_id=self.root.id,app_code='neostaffing').first())
        self.assertEqual(digest(inventory()),before)

    def _sign_in_client(self, client, user):
        from flask import g
        with client.session_transaction() as session:
            session['_user_id'] = str(user.id)
            session['_fresh'] = True
            session['auth_session_version'] = user.auth_session_version
        g.pop('_login_user', None)
        clear_request_cache()

    def test_scoped_pages_require_active_cutover_and_exact_scope_for_get_and_post(self):
        client = self.app.test_client()
        self._sign_in_client(client, self.master)
        for suffix in ('permissions', 'roles'):
            path = f'/neoscorpion/{suffix}'
            self.assertEqual(client.get(path).status_code, 403)
            self.assertEqual(client.post(path).status_code, 403)

        self._sign_in_client(client, self.root)
        self.assertEqual(client.get('/neoapps/permissions').status_code, 200)
        self.assertEqual(client.post('/neoapps/permissions').status_code, 409)

        self.cutover()
        self._sign_in_client(client, self.master)
        before = digest(inventory())
        self.assertEqual(client.get('/neoscorpion/permissions').status_code, 200)
        self.assertEqual(client.get('/neoscorpion/roles').status_code, 200)
        for path in ('/neorain/permissions', '/neorain/roles', '/neogateway/permissions',
                     '/neoapps/permissions', '/admin/permissions', '/motherbrain/permissions',
                     '/neostaffing/permissions', '/neostaffing/accounts'):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 403)
                self.assertEqual(client.post(path).status_code, 403)
        self.assertEqual(digest(inventory()), before)

        grant = management.existing_role(self.master, self.scope)
        grant.is_active = False
        db.session.commit()
        self._sign_in_client(client, self.master)
        for suffix in ('permissions', 'roles'):
            path = f'/neoscorpion/{suffix}'
            self.assertEqual(client.get(path).status_code, 403)
            self.assertEqual(client.post(path).status_code, 403)

    def test_inactive_node_is_omitted_without_breaking_other_admin_pages(self):
        self.cutover()
        client = self.app.test_client()
        NeoNode.query.filter_by(code='rain').one().is_active = False
        db.session.commit()
        self._sign_in_client(client, self.root)
        response = client.get('/neoscorpion/permissions')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'href="/neorain/permissions"', response.data)
        self.assertEqual(client.get('/neorain/permissions').status_code, 404)

    def test_nonadministrative_accounts_cannot_open_or_submit_scoped_editors(self):
        from app.models import FuelDriverIdentity, NeoScorpionVendorAccount

        self.cutover()
        client = self.app.test_client()
        self._sign_in_client(client, self.target)
        for suffix in ('permissions', 'roles'):
            path = f'/neoscorpion/{suffix}'
            self.assertEqual(client.get(path).status_code, 403)
            self.assertEqual(client.post(path).status_code, 403)

        for name, kind in (('scope-driver', 'fuel_driver'), ('scope-vendor', 'vendor_supervisor')):
            # Stale rank information must not override the restricted-account boundary.
            user = fixture_user(name, platform='grandmaster')
            db.session.add(NeoScorpionVendorAccount(
                user_id=user.id, gateway_id=self.gateway.id, invited_by_user_id=self.root.id,
            ))
            db.session.add(FuelDriverIdentity(user_id=user.id, account_type=kind))
            db.session.commit()
            self._sign_in_client(client, user)
            account = client.get('/my-account')
            self.assertEqual(account.status_code, 200)
            self.assertNotIn(b'Roles &amp; Permissions', account.data)
            for path in ('/neoscorpion/permissions', '/neoscorpion/roles', '/neoapps/permissions'):
                with self.subTest(account_type=kind, path=path):
                    self.assertEqual(client.get(path).status_code, 403)
                    self.assertEqual(client.post(path).status_code, 403)

    def test_nightly_fueler_projection_honors_explicit_revocation_and_local_threshold(self):
        from app.services.neoscorpion_assets import eligible_nightly_fueler_users
        self.cutover()
        self.assertIn(self.target.id,[u.id for u in eligible_nightly_fueler_users(self.gateway)])
        self.set_role('disabled')
        app=PortalAppAccess.query.filter_by(user_id=self.target.id,app_code='neogateway').one();app.role='master'
        db.session.commit();clear_request_cache()
        self.assertNotIn(self.target.id,[u.id for u in eligible_nightly_fueler_users(self.gateway)])
        self.set_role('operator')
        key='neoscorpion.fueler.edit'
        rule=PermissionRule.query.filter_by(permission_key=key).one()
        management.set_policy(self.scope,management.policy_review(self.scope),[(rule.id,'master')]);db.session.commit();clear_request_cache()
        self.assertNotIn(self.target.id,[u.id for u in eligible_nightly_fueler_users(self.gateway,permission_key=key)])

    def test_vendor_and_inactive_account_never_obtain_scoped_administration(self):
        from app.models import NeoScorpionVendorAccount
        self.cutover()
        db.session.add(NeoScorpionVendorAccount(user_id=self.master.id,gateway_id=self.gateway.id,invited_by_user_id=self.root.id));db.session.commit();clear_request_cache()
        self.assertFalse(can_administer(self.master,self.scope));self.assertFalse(user_can('neoscorpion.fuel_dispatch.edit',self.master))
        self.target.is_active=False;db.session.commit();clear_request_cache()
        self.assertFalse(can_administer(self.target,self.scope));self.assertFalse(user_can('neogateway.landing.view',self.target))

    def test_scope_commissioning_never_launches_neobid_or_accepts_unknown_scope(self):
        self.cutover()
        app=PortalAppAccess(user_id=self.target.id,app_code='neobid',role='watcher',status='approved',is_active=True)
        db.session.add(app);db.session.commit();clear_request_cache()
        scope=Scope('application','neobid')
        with self.assertRaises(ValueError):management.commission_scope(self.target.id,scope,review_token(self.target),PASSWORD,f'COMMISSION {self.target.id} {scope.key}','Unavailable application')
        invalid=Scope('gateway','neogateway',999)
        with self.assertRaises(ValueError):management.set_role(self.target.id,invalid,'operator',review_token(self.target),'Unknown gateway')
        self.assertEqual(AccountSecurityEvent.query.filter_by(action='scoped_grandmaster_commissioned').count(),0)

    def test_platform_peers_and_last_administrator_are_not_ordinary_role_edits(self):
        self.cutover();scope=Scope('neoapps')
        for target in (self.root,self.backup):
            with self.subTest(target=target.id),self.assertRaises(ValueError):
                management.set_role(target.id,scope,'watcher',review_token(target),'No ordinary Grandmaster removal')
        self.assertEqual(User.query.filter_by(role='grandmaster',is_active=True).count(),2)
        self.assertEqual(AccountSecurityEvent.query.filter_by(action='scoped_role_changed').count(),0)

    def test_application_revocation_applies_to_every_gateway(self):
        from app.services.access_control import user_has_gateway_access
        self.cutover()
        other=Gateway(code='ALT',name='Synthetic second gateway',is_active=True);db.session.add(other);db.session.flush()
        member=GatewayMembership(user_id=self.target.id,gateway_id=other.id,status='approved',is_active=True)
        db.session.add(member);db.session.flush()
        db.session.add(GatewayNodeRole(gateway_membership_id=member.id,node_id=self.node.id,role='operator',is_active=True));db.session.commit();clear_request_cache()
        self.assertEqual(get_user_node_role(self.target,'ALT','scorpion'),'operator')
        management.decide_application(self.target.id,Scope('application','neogateway'),'watcher','revoked',review_token(self.target),'Revoke application at every gateway')
        db.session.commit();clear_request_cache()
        self.assertFalse(user_has_gateway_access(self.target,'ALT'))
        self.assertIsNone(get_user_node_role(self.target,'ALT','scorpion'))
        self.assertFalse(read_authority(self.target.id).can_administer(Scope('node','neogateway',other.id,self.node.id)))
