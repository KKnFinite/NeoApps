"""Isolated policy, authority, evidence and controlled-worker contracts."""
import copy
import json
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch, Mock

from app.extensions import db
from flask import g
from app.models import AccountAuthorizationState, AccountSecurityEvent, FrostHistoryTask, Gateway, PermissionRule, PortalAppAccess
from app.services import frost_intelligence as intelligence, frost_inference, frost_auto
from tests import test_account_security_phase1 as fixtures
from scripts import frost_retrain


class IntelligenceTest(TestCase):
    setUp = fixtures.AccountSecurityPhase1Test.setUp
    tearDown = fixtures.AccountSecurityPhase1Test.tearDown
    login = fixtures.AccountSecurityPhase1Test.login
    path = '/neosubzero/frost-vault/intelligence-settings'

    def owner(self):
        actor = fixtures.fixture_user('frost-owner', platform='grandmaster')
        self.login(actor)
        self.gateway = Gateway.query.filter_by(code='RFD').one()
        return actor

    def post(self, revision=0, **changes):
        values = {key: False for key in intelligence.KEYS}
        values.update(changes)
        return self.client.post(self.path, json=dict(values, expected_revision=revision))

    def test_off_defaults_read_only_and_persist_across_sessions(self):
        self.owner()
        before = FrostHistoryTask.query.count(), AccountSecurityEvent.query.count()
        first = self.client.get(self.path)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.headers['Cache-Control'], 'no-store')
        self.assertTrue(all(first.json['settings'][key] is False for key in intelligence.KEYS))
        self.assertEqual((FrostHistoryTask.query.count(), AccountSecurityEvent.query.count()), before)
        page = self.client.get('/neosubzero/settings')
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.data.count(b'role="switch"'), 3)
        self.assertIn(b'SAVE FROST INTELLIGENCE', page.data)
        self.assertEqual(self.post(learn_from_live_operations=True, automatic_retraining=True).status_code, 200)
        db.session.remove()
        other = self.app.test_client()
        with self.client.session_transaction() as session:
            identity = dict(session)
        with other.session_transaction() as session:
            session.update(identity)
        g.pop('_login_user', None)
        self.assertTrue(other.get(self.path).json['settings']['automatic_retraining'])
        audit = AccountSecurityEvent.query.filter_by(action='frost_intelligence_changed').one()
        self.assertEqual(audit.details['changes']['automatic_retraining'], dict(previous=False, new=True))
        self.assertEqual(audit.details['before']['revision'], 0)
        self.assertEqual(audit.details['after']['revision'], 1)
        self.assertIsNotNone(audit.created_at)
        self.assertEqual(self.post(revision=1, learn_from_live_operations=True, automatic_retraining=True).status_code, 200)
        self.assertEqual(AccountSecurityEvent.query.count(), 1)

    def test_node_and_application_authority_never_grant_toggle_permission(self):
        rule = PermissionRule.query.filter_by(permission_key='neosubzero.settings.view').one()
        rule.minimum_role = 'watcher'
        for role in ('master', 'grandmaster'):
            actor = fixtures.fixture_user('node-' + role, nodes={'subzero': role})
            self.login(actor)
            page = self.client.get('/neosubzero/settings')
            self.assertEqual(page.status_code, 200)
            self.assertIn(b'FROST INTELLIGENCE', page.data)
            self.assertNotIn(b'SAVE FROST INTELLIGENCE', page.data)
            self.assertEqual(self.post(automatic_retraining=True).status_code, 403)
            self.assertEqual(self.client.post(self.path, data={'automatic_retraining':'on','expected_revision':'0'}).status_code, 403)
        actor = fixtures.fixture_user('application-grandmaster', platform='master', nodes={'subzero':'master'})
        PortalAppAccess.query.filter_by(user_id=actor.id,app_code='neogateway').one().role='grandmaster'
        self.login(actor)
        self.assertEqual(self.post(automatic_retraining=True).status_code,403)
        self.assertEqual(FrostHistoryTask.query.filter_by(kind=intelligence.KIND).count(), 0)
        self.assertEqual(AccountSecurityEvent.query.count(), 0)

    def test_verified_platform_authority_with_scoped_cutover_and_revocation(self):
        actor = self.owner()
        audit = AccountSecurityEvent(actor_user_id=actor.id, subject_user_id=actor.id,
            action='synthetic_scope_activation', details={})
        db.session.add(audit)
        db.session.flush()
        db.session.add(AccountAuthorizationState(id=1, active=True, security_event_id=audit.id, inventory_fingerprint='a'*64))
        db.session.commit()
        self.assertEqual(self.post(automatic_retraining=True).status_code, 200)
        actor.role = 'watcher'
        db.session.commit()
        self.assertEqual(self.post(revision=1).status_code, 403)
        self.assertTrue(intelligence.read(self.gateway.id)['automatic_retraining'])
        self.assertEqual(AccountSecurityEvent.query.filter_by(action='frost_intelligence_changed').count(), 1)

    def test_unverified_grandmaster_and_fresh_locked_authority_are_required(self):
        actor = self.owner()
        actor.email_verified_at = None
        db.session.commit()
        self.assertEqual(self.post(automatic_retraining=True).status_code, 403)
        actor.email_verified_at = datetime.utcnow()
        db.session.commit()
        def revoked():
            actor.role = 'watcher'
            return actor
        with patch.object(intelligence, 'lock_administration', side_effect=revoked):
            self.assertEqual(self.post(automatic_retraining=True).status_code, 403)
        self.assertEqual(AccountSecurityEvent.query.count(), 0)

    def test_operational_activation_requires_confirmation_and_compatible_approval_atomically(self):
        self.owner()
        response = self.post(automatic_retraining=True, operational_predictions=True)
        self.assertEqual(response.status_code, 409)
        self.assertIn('confirm', response.json['error'])
        response = self.client.post(self.path, json=dict(expected_revision=0, confirm_operational=True,
            **{key:True for key in intelligence.KEYS}))
        self.assertEqual(response.status_code, 409)
        self.assertIn('compatible', response.json['error'])
        self.assertTrue(all(intelligence.read(self.gateway.id)[key] is False for key in intelligence.KEYS))
        self.assertEqual(AccountSecurityEvent.query.count(), 0)
        with patch('app.services.frost_model_registry.current_release', return_value=SimpleNamespace(artifact_id=9)):
            self.assertIn('research-only', intelligence.operational_prerequisite(self.gateway.id))

    def test_stale_and_malformed_requests_rollback_and_csrf_is_enforced(self):
        self.owner()
        self.assertEqual(self.post(automatic_retraining='true').status_code, 409)
        self.assertEqual(self.client.post(self.path, json=[]).status_code, 400)
        self.assertEqual(self.post(automatic_retraining=True).status_code, 200)
        self.assertEqual(self.post(learn_from_live_operations=True).status_code, 409)
        self.assertFalse(intelligence.read(self.gateway.id)['learn_from_live_operations'])
        self.assertEqual(AccountSecurityEvent.query.count(), 1)
        with patch.dict(self.app.config, {'CSRF_PROTECT_TESTING':True}):
            self.assertEqual(self.post(revision=1).status_code, 400)

    def test_toggle_isolation_preserves_research_weather_and_no_operational_scoring(self):
        self.owner()
        self.assertTrue(frost_auto.enabled(self.gateway))
        with patch.object(frost_inference, 'approved', side_effect=AssertionError('Operational scoring must not load research models')):
            result = frost_inference.predict(self.gateway.id, operational=True)
            self.assertFalse(result['enabled'])
            self.assertIn('OFF', result['state'])
        with patch.object(frost_inference, 'approved', return_value=(None,None)) as research:
            self.assertEqual(frost_inference.predict(self.gateway.id)['state'], 'NO APPROVED MODEL')
            research.assert_called_once()
        self.assertTrue(self.post(learn_from_live_operations=True).json['settings']['learn_from_live_operations'])
        self.assertTrue(frost_auto.enabled(self.gateway))

    def test_future_verified_inspection_only_and_old_testing_never_becomes_eligible(self):
        self.owner()
        base = datetime(2027,1,1,tzinfo=timezone.utc)
        operation = SimpleNamespace(gateway_id=self.gateway.id, created_at=base.replace(tzinfo=None)+timedelta(seconds=1))
        payload = dict(record_type='direct_aircraft_frost_inspection', result='present', observer_user_id=3,
            source='explicit direct visual inspection attestation', evidence_strength='direct_physical_inspection',
            observed_at_utc=(base+timedelta(seconds=2)).isoformat(), weather={'source_sha256':'a'*64})
        old = intelligence.stamp_learning(operation, payload)
        self.assertFalse(old['learning_eligible'])
        self.assertTrue(old['testing_phase'])
        policy = dict(intelligence.read(self.gateway.id), learn_from_live_operations=True, learning_since=base.isoformat(), revision=1)
        with patch.object(intelligence, 'read', return_value=policy):
            valid = intelligence.stamp_learning(operation, payload)
            self.assertTrue(valid['learning_eligible'])
            for changes in ({'weather':None}, {'result':'uncertain'}, {'record_type':'actual_fluid_application'}, {'testing_phase':True}):
                self.assertFalse(intelligence.stamp_learning(operation, dict(payload,**changes))['learning_eligible'])
            self.assertFalse(intelligence.stamp_learning(operation, payload, previous=old)['learning_eligible'])
            operation.created_at = base.replace(tzinfo=None)-timedelta(days=1)
            self.assertFalse(intelligence.stamp_learning(operation, payload)['learning_eligible'])
        self.assertFalse(intelligence.stamp_learning(operation, payload, previous=valid)['learning_eligible'])


class ControlledTrainingTest(TestCase):
    def policy(self, enabled=True, revision=1):
        return dict(schema=intelligence.VERSION,gateway_id=7,revision=revision,
            automatic_retraining=enabled,learn_from_live_operations=False,operational_predictions=False,learning_since=None)

    def remote(self, *policies):
        return SimpleNamespace(request=Mock(side_effect=[json.dumps(dict(ok=True,settings=p)).encode() for p in policies]))

    def test_off_never_prepares_or_trains_and_render_is_always_forbidden(self):
        with patch.object(frost_retrain.local,'prepare',side_effect=AssertionError('No dataset work while OFF')):
            with self.assertRaisesRegex(ValueError,'OFF'):
                frost_retrain.once(self.remote(self.policy(False)), 'unused', 'instance/frost-policy-test', 'candidate')
        with patch.dict('os.environ',{'RENDER':'true'}):
            with self.assertRaisesRegex(RuntimeError,'workstation-only'):
                frost_retrain.once(self.remote(self.policy()), 'unused', 'instance/frost-policy-test', 'candidate')

    def test_candidate_only_and_disable_discards_inflight_result(self):
        data={'manifest':{'gateway_id':7}}
        artifact={'training':{},'model_version':'candidate'}
        policy=self.policy()
        with tempfile.TemporaryDirectory() as folder, patch.object(frost_retrain.local,'prepare',return_value=data), \
                patch.object(frost_retrain.local,'train',return_value=artifact) as train:
            result=frost_retrain.once(self.remote(policy,policy,policy),'dataset',folder,'candidate')
            self.assertFalse(result['approved'])
            saved=json.loads(Path(result['path']).read_bytes())
            self.assertFalse(saved['operationally_validated'])
            self.assertEqual(saved['training']['automatic_control']['revision'],1)
            with self.assertRaises(ValueError):
                frost_retrain.once(self.remote(policy),'dataset',folder,'candidate')
            self.assertEqual(train.call_count,1)
            with self.assertRaisesRegex(ValueError,'OFF'):
                frost_retrain.once(self.remote(policy,policy,self.policy(False,2)),'dataset',folder,'discarded')
            self.assertFalse(Path(folder,'discarded.json').exists())
            self.assertTrue(Path(result['path']).exists())
