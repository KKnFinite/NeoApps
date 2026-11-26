"""Real transactions in retained, isolated loopback schemas; no live database."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier,Event
import os
import unittest
from flask_login import login_user
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError
from app.extensions import db
from app.models import User,NeoNode,PortalAppAccess,AccountAuthorizationState,AccountSecurityEvent,PermissionRule,AccountScopedPolicy
from app.services.account_scope_cutover import review,activate,rollback,rollback_review
from app.services.account_scope_runtime import scoped_active
from app.services.account_authority import Scope
from app.services.accounts_workspace import review_token
from app.services import scoped_access_management as management
from tests import test_account_foundation_postgres as pg
from tests.test_scoped_authorization import prepare_scope_fixture
from tests.test_account_security_phase1 import PASSWORD


@unittest.skipUnless(os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL'),'Requires disposable loopback PostgreSQL')
class ScopedAuthorizationPostgresTest(unittest.TestCase):
    def setUp(self):
        pg.AccountFoundationPostgresTest.setUpClass.__func__(type(self))
        with self.app.app_context():
            root,backup,target,master,_=prepare_scope_fixture()
            self.root_id,self.backup_id,self.target_id,self.master_id=root.id,backup.id,target.id,master.id
            self.scope=Scope('node','neogateway',1,NeoNode.query.filter_by(code='scorpion').one().id)

    def tearDown(self):
        pg.AccountFoundationPostgresTest.tearDownClass.__func__(type(self))

    def run_as(self,user_id,callback):
        with self.app.test_request_context():
            login_user(db.session.get(User,user_id))
            try:
                result=callback();db.session.commit();return result
            except (ValueError,DBAPIError) as error:
                db.session.rollback();return type(error).__name__

    def cutover(self):
        result=self.run_as(self.root_id,lambda:activate(review()['token'],PASSWORD,'ACTIVATE SCOPED PERMISSIONS'))
        self.assertIsInstance(result,int,result)

    def test_deployment_reconciles_complete_eligible_matrix_and_audit_atomically(self):
        from app.models import Gateway, GatewayMembership, GatewayNodeRole, NeoScorpionVendorAccount
        from app.services.node_watcher_provisioning import REPAIR_ACTION, reconcile_watcher_nodes
        from tests.neoraiden_fixtures import record_counts
        from tests.test_neoraiden import seed_sort

        self.cutover()
        with self.app.app_context():
            gateway = Gateway.query.filter_by(code="RFD").one()
            root = db.session.get(User, self.root_id)
            target = db.session.get(User, self.target_id)
            member = GatewayMembership.query.filter_by(user_id=target.id).one()
            sektor_id = NeoNode.query.filter_by(code="sektor").one().id
            roles = GatewayNodeRole.query.filter_by(gateway_membership_id=member.id)
            roles.filter_by(node_id=sektor_id).delete()
            disabled = roles.filter_by(node_id=NeoNode.query.filter_by(code="rain").one().id).one()
            disabled.is_active = False
            denied = PortalAppAccess.query.filter_by(user_id=self.master_id, app_code="neogateway").one()
            denied.is_active = False
            db.session.add(NeoScorpionVendorAccount(user_id=self.backup_id, gateway_id=gateway.id,
                                                   invited_by_user_id=root.id, enabled=True))
            vendor_member = GatewayMembership.query.filter_by(user_id=self.backup_id).one()
            GatewayNodeRole.query.filter_by(gateway_membership_id=vendor_member.id, node_id=sektor_id).delete()
            operation = seed_sort(gateway)
            record_counts(gateway, operation)
            db.session.commit()

            def fail(_mapper, _connection, row):
                if row.action == REPAIR_ACTION:
                    raise RuntimeError("Injected reconciliation receipt failure")
            event.listen(AccountSecurityEvent, "before_insert", fail)
            try:
                with self.assertRaisesRegex(RuntimeError, "receipt failure"):
                    reconcile_watcher_nodes(root, gateway)
                db.session.rollback()
            finally:
                event.remove(AccountSecurityEvent, "before_insert", fail)
            self.assertIsNone(roles.filter_by(node_id=sektor_id).first())
            summary, verification = reconcile_watcher_nodes(root, gateway)
            db.session.commit()
            self.assertEqual(summary["eligible_accounts"], 3)
            self.assertEqual(summary["assignments_corrected"], 1)
            self.assertEqual(summary["missing_after"], 0)
            self.assertEqual(summary["explicit_disabled"], 1)
            self.assertGreater(summary["higher_roles"], 0)
            self.assertEqual(roles.filter_by(node_id=sektor_id).one().role, "watcher")
            self.assertFalse(disabled.is_active)
            self.assertFalse(denied.is_active)
            self.assertIsNone(GatewayNodeRole.query.filter_by(gateway_membership_id=vendor_member.id, node_id=sektor_id).first())
            self.assertTrue(verification["dashboard"] and verification["live_counts"])
            self.assertFalse(verification["ebm_edit"])
            self.assertTrue(verification["uld_matches_higher_role"])
            self.assertEqual(reconcile_watcher_nodes(root, gateway), (summary, verification))
            self.assertEqual(AccountSecurityEvent.query.filter_by(action=REPAIR_ACTION).count(), 1)

    def test_concurrent_cutovers_commit_one_complete_mapping(self):
        token=self.run_as(self.root_id,lambda:review()['token']);start=Barrier(2)
        def apply():
            start.wait(5)
            return self.run_as(self.root_id,lambda:activate(token,PASSWORD,'ACTIVATE SCOPED PERMISSIONS'))
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:apply(),range(2)))
        self.assertEqual(sum(type(r) is int for r in results),1,results);self.assertIn('AccountConflict',results)
        with self.app.app_context():
            self.assertTrue(scoped_active())
            self.assertEqual(AccountSecurityEvent.query.filter_by(action='scoped_authorization_activated').count(),1)
            self.assertEqual(management.existing_role(db.session.get(User,self.root_id),self.scope).role,'master')

    def test_concurrent_target_changes_have_one_receipt_and_epoch(self):
        self.cutover();token=self.run_as(self.root_id,lambda:review_token(db.session.get(User,self.target_id)));start=Barrier(2)
        def save(role):
            start.wait(5)
            def action():management.set_role(self.target_id,self.scope,role,token,'Concurrent review');return 'saved'
            return self.run_as(self.root_id,action)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(save,('simulator','watcher')))
        self.assertEqual(results.count('saved'),1,results);self.assertIn('AccountConflict',results)
        with self.app.app_context():
            self.assertEqual(db.session.get(User,self.target_id).auth_session_version,2)
            self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=self.target_id,action='scoped_role_changed').count(),1)

    def test_revoked_actor_is_rechecked_after_lock_wait(self):
        self.check_authority_change_during_lock_wait(require_factor=False)

    def test_actor_requiring_a_second_factor_is_rechecked_after_lock_wait(self):
        self.check_authority_change_during_lock_wait(require_factor=True)

    def check_authority_change_during_lock_wait(self, *, require_factor):
        self.cutover();token=self.run_as(self.master_id,lambda:review_token(db.session.get(User,self.target_id)))
        waiting=Event()
        def before(_conn,_cursor,statement,*_args):
            if 'neo_nodes' in statement and 'FOR UPDATE' in statement:waiting.set()
        with self.app.app_context():
            NeoNode.query.filter_by(code='motherbrain').with_for_update().one()
            event.listen(self.engine,'before_cursor_execute',before)
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future=pool.submit(self.run_as,self.master_id,lambda:management.set_role(self.target_id,self.scope,'simulator',token,'Waited review'))
                    self.assertTrue(waiting.wait(5))
                    actor=db.session.get(User,self.master_id)
                    if require_factor:
                        actor.mfa_required = True
                    else:
                        actor.auth_session_version+=1
                        management.existing_role(actor,self.scope).is_active=False
                    db.session.commit()
                    self.assertEqual(future.result(timeout=12),'ValueError')
            finally:event.remove(self.engine,'before_cursor_execute',before);db.session.rollback()
            self.assertEqual(AccountSecurityEvent.query.filter_by(action='scoped_role_changed').count(),0)

    def test_concurrent_approval_denial_retains_one_decision(self):
        self.cutover()
        with self.app.app_context():
            db.session.add(PortalAppAccess(user_id=self.target_id,app_code='neostaffing',status='pending',is_active=True,role='watcher'));db.session.commit()
        token=self.run_as(self.root_id,lambda:review_token(db.session.get(User,self.target_id)));start=Barrier(2)
        scope=Scope('application','neostaffing')
        def decide(outcome):
            start.wait(5)
            def action():management.decide_application(self.target_id,scope,'operator',outcome,token,'Concurrent application decision');return 'saved'
            return self.run_as(self.root_id,action)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(decide,('approved','denied')))
        self.assertEqual(results.count('saved'),1,results);self.assertIn('AccountConflict',results)
        with self.app.app_context():self.assertEqual(AccountSecurityEvent.query.filter_by(action='scoped_application_access_changed').count(),1)

    def test_failed_audit_rolls_back_role_and_session(self):
        self.cutover();token=self.run_as(self.root_id,lambda:review_token(db.session.get(User,self.target_id)))
        def fail(_mapper,_connection,row):
            if row.action=='scoped_role_changed':raise ValueError('Injected receipt failure')
        event.listen(AccountSecurityEvent,'before_insert',fail)
        try:
            result=self.run_as(self.root_id,lambda:management.set_role(self.target_id,self.scope,'simulator',token,'Rollback review'))
            self.assertEqual(result,'ValueError')
        finally:event.remove(AccountSecurityEvent,'before_insert',fail)
        with self.app.app_context():
            target=db.session.get(User,self.target_id)
            self.assertEqual(management.existing_role(target,self.scope).role,'operator');self.assertEqual(target.auth_session_version,1)
            self.assertEqual(AccountSecurityEvent.query.filter_by(action='scoped_role_changed').count(),0)

    def test_concurrent_policy_changes_use_one_exact_scope_override(self):
        self.cutover()
        with self.app.app_context():rule_id=PermissionRule.query.filter_by(permission_key='neoscorpion.fueler.edit').one().id
        token=self.run_as(self.master_id,lambda:management.policy_review(self.scope));start=Barrier(2)
        def save(role):
            start.wait(5)
            def action():management.set_policy(self.scope,token,[(rule_id,role)]);return 'saved'
            return self.run_as(self.master_id,action)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(save,('simulator','master')))
        self.assertEqual(results.count('saved'),1,results);self.assertIn('AccountConflict',results)
        with self.app.app_context():
            self.assertEqual(AccountScopedPolicy.query.one().scope_key,self.scope.key)
            self.assertEqual(db.session.get(PermissionRule,rule_id).minimum_role,'operator')
            self.assertEqual(AccountSecurityEvent.query.filter_by(action='scoped_permission_changed').count(),1)

    def test_operator_threshold_saves_and_forged_protected_or_out_of_scope_edit_rolls_back(self):
        self.cutover()
        with self.app.app_context():
            editable = PermissionRule.query.filter_by(permission_key='neoscorpion.fueler.edit').one().id
            protected = PermissionRule.query.filter_by(permission_key='neoscorpion.refresh_settings.edit').one().id
        def save(updates):
            management.set_policy(self.scope, management.policy_review(self.scope), updates)
            return 'saved'
        self.assertEqual(self.run_as(self.master_id, lambda: save([(editable, 'master')])), 'saved')
        self.assertEqual(self.run_as(self.master_id, lambda: save([(editable, 'operator')])), 'saved')
        self.assertEqual(self.run_as(self.master_id, lambda: save([(editable, 'master'), (protected, 'watcher')])), 'ValueError')
        self.assertEqual(self.run_as(self.master_id, lambda: save([(editable, 'watcher')])), 'ValueError')
        with self.app.app_context():
            override = AccountScopedPolicy.query.one()
            self.assertEqual((override.scope_key, override.minimum_role), (self.scope.key, 'operator'))
            self.assertEqual(db.session.get(PermissionRule, editable).minimum_role, 'operator')
            self.assertEqual(AccountSecurityEvent.query.filter_by(action='scoped_permission_changed').count(), 2)

    def test_rollback_race_cannot_overwrite_a_committed_role(self):
        self.cutover();rollback_token=self.run_as(self.root_id,lambda:rollback_review()['token'])
        role_token=self.run_as(self.root_id,lambda:review_token(db.session.get(User,self.target_id)));start=Barrier(2)
        def revert():
            start.wait(5)
            return self.run_as(self.root_id,lambda:rollback(rollback_token,PASSWORD,'ROLL BACK SCOPED PERMISSIONS'))
        def change():
            start.wait(5)
            def action():management.set_role(self.target_id,self.scope,'simulator',role_token,'Concurrent scoped edit');return 'saved'
            return self.run_as(self.root_id,action)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a,b=pool.submit(revert),pool.submit(change);results=[a.result(timeout=15),b.result(timeout=15)]
        with self.app.app_context():
            target=db.session.get(User,self.target_id)
            if scoped_active():
                self.assertEqual(results,['AccountConflict','saved']);self.assertEqual(management.existing_role(target,self.scope).role,'simulator')
            else:
                self.assertIsInstance(results[0],int);self.assertIn(results[1],('FoundationNotActivated','AccountConflict'))
                self.assertEqual(management.existing_role(target,self.scope).role,'operator')
