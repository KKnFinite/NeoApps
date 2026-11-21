"""Unified account decisions on independent, isolated PostgreSQL transactions."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
from unittest import TestCase,skipUnless
from unittest.mock import patch
from flask_login import login_user
from sqlalchemy import text,inspect
from app.extensions import db
from app.models import User,FuelDriverIdentity,NeoScorpionVendorAccount,AccountSecurityEvent
from app.services import account_types as service
from app.services.accounts_workspace import review_token
from tests import test_account_foundation_postgres as foundation


@skipUnless(foundation.URI,'Requires disposable loopback PostgreSQL')
class UnifiedAccountTypesPostgresTest(TestCase):
    setUpClass=classmethod(foundation.AccountFoundationPostgresTest.setUpClass.__func__)
    tearDownClass=classmethod(foundation.AccountFoundationPostgresTest.tearDownClass.__func__)
    run_as=foundation.AccountFoundationPostgresTest.run_as

    def prepare(self):
        from app.services.permission_rules import ensure_default_permission_rules
        actor,target=foundation.AccountFoundationPostgresTest.prepare(self)
        with self.app.test_request_context():
            login_user(db.session.get(User,actor));ensure_default_permission_rules()
            user=db.session.get(User,target);user.employee_id=str(1000000+target)
            db.session.commit();token=review_token(user)
        return actor,target,token

    def decision(self,actor,target,token,kind='vendor_supervisor'):
        def save():
            user=db.session.get(User,target)
            return service.decide(target,token,kind,'approved',user.employee_id,'yes',None,'').id
        return self.run_as(actor,save)

    def test_competing_types_from_same_review_commit_one_decision(self):
        actor,target,token=self.prepare();barrier=Barrier(2)
        def change(kind):
            barrier.wait(5)
            return self.decision(actor,target,token,kind)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(change,['vendor_supervisor','fuel_driver']))
        self.assertEqual(results.count(target),1,results)
        self.assertIn('AccountConflict',results)
        with self.app.app_context():
            self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=target,action='account_type_decided').count(),1)
            self.assertIsNotNone(db.session.get(NeoScorpionVendorAccount,target))

    def test_failed_final_audit_rolls_back_identity_grants_profile_and_sessions(self):
        actor,target,token=self.prepare()
        with self.app.app_context():before=db.session.get(User,target).auth_session_version
        with patch.object(service,'record_security_change',side_effect=ValueError('Synthetic rejected audit')):
            self.assertEqual(self.decision(actor,target,token),'ValueError')
        with self.app.app_context():
            self.assertEqual(db.session.get(User,target).auth_session_version,before)
            self.assertIsNone(db.session.get(FuelDriverIdentity,target))
            self.assertIsNone(db.session.get(NeoScorpionVendorAccount,target))
            self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=target).count(),0)

    def test_revoked_administrator_cannot_approve_restricted_account(self):
        actor,target,token=self.prepare()
        with self.app.app_context():
            db.session.get(User,actor).role='watcher';db.session.commit()
        with self.assertRaises(PermissionError):self.decision(actor,target,token)
        with self.app.app_context():self.assertIsNone(db.session.get(NeoScorpionVendorAccount,target))

    def test_additive_column_upgrade_is_idempotent_without_account_conversion(self):
        from app.services.schema_sync import sync_database_schema
        actor,target,token=self.prepare()
        with self.app.app_context():
            db.session.execute(text('ALTER TABLE fuel_driver_identities DROP COLUMN account_type'));db.session.commit()
            before=db.session.get(User,target).password_hash,db.session.get(User,target).auth_session_version
            sync_database_schema(self.app);db.session.commit()
            sync_database_schema(self.app);db.session.commit()
            self.assertIn('account_type',{c['name'] for c in inspect(db.engine).get_columns('fuel_driver_identities')})
            self.assertEqual(before,(db.session.get(User,target).password_hash,db.session.get(User,target).auth_session_version))
            self.assertIsNone(db.session.get(FuelDriverIdentity,target))
