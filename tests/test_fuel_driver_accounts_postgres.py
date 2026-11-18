"""Phase 4 real transactions, normalized identities and protected company changes."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import unittest
from flask_login import login_user
from sqlalchemy import text
from app.extensions import db
from app.models import User, FuelDriverIdentity, FuelVendorCompany, FuelVendorMembership, NeoScorpionVendorAccount, AccountSecurityEvent
from app.services import fuel_driver_accounts as service
from app.services.accounts_workspace import review_token
from tests import test_account_foundation_postgres as foundation
from tests.test_account_security_phase1 import PASSWORD, fixture_user


@unittest.skipUnless(foundation.URI,'Requires disposable loopback PostgreSQL')
class FuelDriverAccountsPostgresTest(unittest.TestCase):
    setUpClass=classmethod(foundation.AccountFoundationPostgresTest.setUpClass.__func__)
    tearDownClass=classmethod(foundation.AccountFoundationPostgresTest.tearDownClass.__func__)
    prepare=foundation.AccountFoundationPostgresTest.prepare
    run_as=foundation.AccountFoundationPostgresTest.run_as

    def token(self, actor, target):
        with self.app.test_request_context():
            login_user(db.session.get(User,actor))
            return review_token(db.session.get(User,target))

    def test_two_accounts_cannot_claim_normalized_same_ups_id(self):
        actor,one=self.prepare();_,two=self.prepare()
        tokens=[self.token(actor,id_) for id_ in (one,two)]
        barrier=Barrier(2);value='PG-'+uuid4().hex
        def save(index):
            barrier.wait(5)
            return self.run_as(actor,lambda: service.verify_ups_id((one,two)[index],tokens[index],
                value.upper() if index else value.lower(),'Synthetic concurrent ID verification'))
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(save,(0,1)))
        self.assertEqual(sum(r is None for r in results),1,results)
        with self.app.app_context():
            self.assertEqual(FuelDriverIdentity.query.filter(FuelDriverIdentity.user_id.in_([one,two])).count(),1)

    def test_same_review_cannot_apply_two_identity_corrections(self):
        actor,target=self.prepare();token=self.token(actor,target);barrier=Barrier(2)
        def save(index):
            barrier.wait(5)
            return self.run_as(actor,lambda: service.verify_ups_id(target,token,'PG-'+uuid4().hex,'Synthetic stale identity review'))
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(save,(0,1)))
        self.assertEqual(sum(r is None for r in results),1,results)
        self.assertIn('AccountConflict',results)
        with self.app.app_context():
            self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=target,action='fuel_driver_identity_verified').count(),1)

    def test_company_capability_changes_serialize_and_preserve_history(self):
        actor,target=self.prepare()
        with self.app.test_request_context():
            login_user(db.session.get(User,actor))
            gateway=__import__('app.services.access_control',fromlist=['get_current_gateway']).get_current_gateway()
            db.session.add(NeoScorpionVendorAccount(user_id=target,gateway_id=gateway.id,enabled=True,invited_by_user_id=actor))
            db.session.commit()
            service.verify_ups_id(target,review_token(db.session.get(User,target)),'PG-'+uuid4().hex,'Synthetic verified company driver');db.session.commit()
            companies=[service.create_company(gateway.id,'Company '+uuid4().hex,'Synthetic company attestation').id for _ in range(2)]
            db.session.commit()
        token=self.token(actor,target);barrier=Barrier(2)
        def change(index):
            barrier.wait(5)
            return self.run_as(actor,lambda: service.assign_company(target,token,companies[index],False,
                'Synthetic company association',PASSWORD,target).id)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(change,(0,1)))
        self.assertEqual(sum(isinstance(r,int) for r in results),1,results)
        self.assertIn('AccountConflict',results)
        with self.app.app_context():
            self.assertEqual(FuelVendorMembership.query.filter_by(user_id=target,ended_at=None).count(),1)
            self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=target,action='fuel_vendor_membership_changed').count(),1)

    def test_failed_identity_audit_rolls_back_user_proof_and_session(self):
        from unittest.mock import patch
        actor,target=self.prepare();token=self.token(actor,target)
        with self.app.app_context():
            before=db.session.get(User,target).employee_id,db.session.get(User,target).auth_session_version
        with patch.object(service,'record_security_change',side_effect=ValueError('Synthetic audit rejection')):
            self.assertEqual(self.run_as(actor,lambda: service.verify_ups_id(target,token,'PG-'+uuid4().hex,
                'Synthetic rollback verification')),'ValueError')
        with self.app.app_context():
            user=db.session.get(User,target)
            self.assertEqual((user.employee_id,user.auth_session_version),before)
            self.assertIsNone(db.session.get(FuelDriverIdentity,target))

    def test_revoked_platform_actor_cannot_verify_identity(self):
        actor,target=self.prepare();token=self.token(actor,target)
        with self.app.app_context():
            user=db.session.get(User,actor);user.role='watcher';db.session.commit()
        self.assertEqual(self.run_as(actor,lambda:service.verify_ups_id(target,token,'PG-'+uuid4().hex,
            'Synthetic revoked administration')),'ValueError')
        with self.app.app_context(): self.assertIsNone(db.session.get(FuelDriverIdentity,target))

    def test_supervisor_grant_racing_full_conversion_cannot_leave_latent_authority(self):
        from app.services.neoscorpion_account_types import convert_account
        from app.services.access_control import get_current_gateway
        actor,target=self.prepare()
        with self.app.test_request_context():
            login_user(db.session.get(User,actor));gateway=get_current_gateway()
            db.session.add(NeoScorpionVendorAccount(user_id=target,gateway_id=gateway.id,enabled=True,invited_by_user_id=actor));db.session.commit()
            service.verify_ups_id(target,review_token(db.session.get(User,target)),'PG-'+uuid4().hex,'Synthetic company identity');db.session.commit()
            company=service.create_company(gateway.id,'Company '+uuid4().hex,'Synthetic company attestation');db.session.commit()
            company_id=company.id
        token=self.token(actor,target);barrier=Barrier(2)
        def change(index):
            barrier.wait(5)
            if index==0:
                return self.run_as(actor,lambda:service.assign_company(target,token,company_id,True,'Synthetic supervisor grant',PASSWORD,target).id)
            return self.run_as(actor,lambda:convert_account(get_current_gateway(),db.session.get(User,actor),db.session.get(User,target),'full'))
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(change,(0,1)))
        self.assertIs(results[1],True,results)
        with self.app.app_context():
            self.assertIsNone(db.session.get(NeoScorpionVendorAccount,target))
            self.assertIsNone(service.current_membership(target))
