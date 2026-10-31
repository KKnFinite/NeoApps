"""Atomic reset behavior and rollback in isolated SQLite HTTP requests."""
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch
from sqlalchemy.exc import OperationalError
from app.extensions import db
from app.models import User, UserToken
from app.services import user_tokens as tokens
from tests import test_auth_account_flows as fixtures

class AtomicPasswordResetTest(unittest.TestCase):
    setUp = fixtures.AuthAccountFlowsTest.setUp
    tearDown = fixtures.AuthAccountFlowsTest.tearDown
    _user = fixtures.AuthAccountFlowsTest._user

    def prepare(self):
        user = self._user('atomic-reset', verified=True)
        raw, record = tokens.create_user_token(user, tokens.PASSWORD_RESET)
        db.session.commit()
        return (user, raw, record)

    def submit(self, raw, password='violet harbor lantern signal', confirmation=None):
        return self.client.post('/reset-password/' + raw, data={'password': password, 'confirm_password': password if confirmation is None else confirmation})

    def test_invalid_password_rolls_back_claim_and_link_remains_usable(self):
        user, raw, record = self.prepare()
        initial_hash = user.password_hash
        version = user.auth_session_version
        for password, confirmation in [('short', 'short'), ('violet harbor lantern signal', 'different confirmation'), ('password123!', 'password123!')]:
            self.assertEqual(self.submit(raw, password, confirmation).status_code, 400)
            db.session.refresh(user)
            db.session.refresh(record)
            self.assertIsNone(record.used_at)
            self.assertEqual(user.password_hash, initial_hash)
            self.assertEqual(user.auth_session_version, version)
        self.assertEqual(self.submit(raw).status_code, 302)
        db.session.refresh(record)
        self.assertTrue(record.is_used)

    def test_database_failure_rolls_back_flushed_password_and_all_claims(self):
        user, raw, record = self.prepare()
        initial_hash = user.password_hash
        version = user.auth_session_version
        legacy = UserToken(user_id=user.id, token_hash=tokens.hash_user_token('other-isolated-link'), token_type=tokens.PASSWORD_RESET, expires_at=datetime.utcnow() + timedelta(hours=1))
        db.session.add(legacy)
        db.session.commit()
        with patch('app.auth.routes.db.session.commit', side_effect=OperationalError('simulated commit', {}, Exception('isolated failure'))):
            self.assertEqual(self.submit(raw).status_code, 500)
        db.session.refresh(user)
        db.session.refresh(record)
        db.session.refresh(legacy)
        self.assertEqual(user.password_hash, initial_hash)
        self.assertEqual(user.auth_session_version, version)
        self.assertIsNone(record.used_at)
        self.assertIsNone(legacy.used_at)
        self.assertEqual(self.submit(raw).status_code, 302)

    def test_failure_during_revocation_rolls_back_claim_and_password(self):
        user, raw, record = self.prepare()
        initial_hash = user.password_hash
        with patch('app.auth.routes.revoke_unused_password_reset_tokens', side_effect=OperationalError('simulated update', {}, Exception('isolated failure'))):
            self.assertEqual(self.submit(raw).status_code, 500)
        db.session.refresh(user)
        db.session.refresh(record)
        self.assertEqual(user.password_hash, initial_hash)
        self.assertIsNone(record.used_at)
        self.assertEqual(self.submit(raw).status_code, 302)

    def test_expired_used_unknown_and_wrong_type_links_do_not_change_password(self):
        user, raw, record = self.prepare()
        initial_hash = user.password_hash
        record.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()
        self.assertEqual(self.submit(raw).status_code, 400)
        record.expires_at = datetime.utcnow() + timedelta(hours=1)
        record.used_at = datetime.utcnow()
        db.session.commit()
        self.assertEqual(self.submit(raw).status_code, 400)
        self.assertEqual(self.submit('not-a-real-link').status_code, 400)
        verification, verification_record = tokens.create_user_token(user, tokens.EMAIL_VERIFICATION)
        db.session.commit()
        self.assertEqual(self.submit(verification).status_code, 400)
        db.session.refresh(user)
        db.session.refresh(verification_record)
        self.assertEqual(user.password_hash, initial_hash)
        self.assertIsNone(verification_record.used_at)

    def test_get_never_consumes_token_and_post_cannot_replay(self):
        user, raw, record = self.prepare()
        version = user.auth_session_version
        self.assertEqual(self.client.get('/reset-password/' + raw).status_code, 200)
        self.assertIsNone(record.used_at)
        self.assertEqual(self.submit(raw).status_code, 302)
        self.assertEqual(self.submit(raw, 'different harbor lantern signal').status_code, 400)
        db.session.refresh(user)
        self.assertEqual(user.auth_session_version, version + 1)
        self.assertTrue(user.check_password('violet harbor lantern signal'))
        self.assertFalse(user.check_password('different harbor lantern signal'))

    def test_success_preserves_verification_links_and_clears_reset_flags(self):
        user, raw, record = self.prepare()
        verify, verification = tokens.create_user_token(user, tokens.EMAIL_VERIFICATION)
        user.password_reset_required = True
        user.password_policy_update_required = True
        user.temporary_password_expires_at = datetime.utcnow() + timedelta(hours=1)
        db.session.commit()
        self.assertEqual(self.submit(raw).status_code, 302)
        db.session.refresh(user)
        db.session.refresh(verification)
        self.assertFalse(user.password_reset_required)
        self.assertFalse(user.password_policy_update_required)
        self.assertIsNone(user.temporary_password_expires_at)
        self.assertIsNone(verification.used_at)
        self.assertIsNotNone(tokens.get_valid_token_record(verify, tokens.EMAIL_VERIFICATION))
