"""Deletion races/rollback on a new schema in the existing synthetic database."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest import TestCase, skipUnless
from unittest.mock import patch

from flask_login import login_user
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.extensions import db
from app.models import User, NeoNode, AccountSecurityEvent,PortalAppAccess
from app.services.account_deletion import delete_account,ACTION
from app.services.accounts_workspace import review_token
from tests import test_account_foundation_postgres as fixture


@skipUnless(fixture.URI,'Requires disposable loopback PostgreSQL')
class AccountDeletionPostgresTest(TestCase):
    @classmethod
    def setUpClass(cls):fixture.AccountFoundationPostgresTest.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls):fixture.AccountFoundationPostgresTest.tearDownClass.__func__(cls)
    prepare=fixture.AccountFoundationPostgresTest.prepare

    def token(self,actor,target):
        with self.app.test_request_context():
            login_user(db.session.get(User,actor))
            return review_token(db.session.get(User,target))

    def remove(self,actor,target,token,ready=None):
        with self.app.test_request_context():
            login_user(db.session.get(User,actor))
            if ready:ready.set()
            try:
                delete_account(target,token,'yes',str(target));db.session.commit()
                return 'deleted'
            except (ValueError,PermissionError,DBAPIError) as error:
                db.session.rollback();return type(error).__name__

    def test_duplicate_deletion_commits_once_with_immutable_identity(self):
        actor,target=self.prepare();token=self.token(actor,target);barrier=Barrier(2)
        def run():
            barrier.wait(timeout=10);return self.remove(actor,target,token)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(run) for _ in range(2)]
            self.assertEqual(sorted(f.result(timeout=25) for f in futures),['AccountConflict','deleted'])
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(User,target))
            self.assertFalse(db.session.get(User,target).is_active)
            self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=target,action=ACTION).count(),1)

    def test_revoked_administrator_cannot_finish_waiting_deletion(self):
        actor,target=self.prepare();token=self.token(actor,target);ready=Event()
        with self.app.app_context():
            NeoNode.query.filter_by(code='motherbrain').with_for_update().one()
            user=db.session.get(User,actor);user.role='watcher';db.session.flush()
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending=pool.submit(self.remove,actor,target,token,ready)
                self.assertTrue(ready.wait(timeout=10))
                db.session.commit()
                self.assertIn(pending.result(timeout=25),('PermissionError','ValueError'))
            self.assertTrue(db.session.get(User,target).is_active)
            self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=target,action=ACTION).count(),0)

    def test_post_audit_failure_rolls_back_all_security_changes(self):
        actor,target=self.prepare();token=self.token(actor,target)
        with patch('app.services.user_tokens.revoke_unused_password_reset_tokens',side_effect=ValueError('Rollback proof')):
            self.assertEqual(self.remove(actor,target,token),'ValueError')
        with self.app.app_context():
            self.assertTrue(db.session.get(User,target).is_active)
            self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=target,action=ACTION).count(),0)
            self.assertTrue(all(row.is_active for row in PortalAppAccess.query.filter_by(user_id=target)))

    def test_database_rejects_deletion_receipt_update_and_delete(self):
        actor,target=self.prepare();token=self.token(actor,target)
        self.assertEqual(self.remove(actor,target,token),'deleted')
        with self.app.app_context():
            audit=AccountSecurityEvent.query.filter_by(subject_user_id=target,action=ACTION).one().id
            for statement in ('UPDATE account_security_events SET action=\'forged\' WHERE id=:id',
                              'DELETE FROM account_security_events WHERE id=:id'):
                with self.assertRaises(DBAPIError):db.session.execute(text(statement),{'id':audit})
                db.session.rollback()
                self.assertEqual(db.session.get(AccountSecurityEvent,audit).action,ACTION)
