"""Phase 2 is a dormant foundation, not a role migration or UI cutover."""
from dataclasses import replace
from uuid import uuid4
from unittest import TestCase

from flask_login import login_user, logout_user
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app import create_app
from app.extensions import db
from app.models import AccountAccessDecision, AccountAccessRequest, AccountScopedRole, AccountSecurityEvent, Gateway, GatewayMembership, GatewayNodeRole, NeoNode, PortalAppAccess, User
from app.services.access_control import ensure_default_gateway_and_nodes, prime_user_node_roles_for_request
from app.services.account_authority import Authority, Scope, compare_authority, read_authority
from app.services.account_foundation import AccountConflict, FoundationNotActivated, decide_access, entitlement_fingerprint, fulfill_access_request, request_access, stage_role
from app.services.account_foundation_schema import ensure_account_history_guards
from app.services.permission_rules import ensure_default_permission_rules, user_can
from tests.test_account_security_phase1 import fixture_user


class AccountFoundationTest(TestCase):
    def setUp(self):
        self.app = create_app(type("Config", (), dict(
            SECRET_KEY="phase2-test", TESTING=True, AUTO_BOOTSTRAP_DATABASE=False,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:", SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )))
        self.context = self.app.test_request_context()
        self.context.push()
        db.create_all()
        ensure_account_history_guards(db.session.connection())
        ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        self.admin = fixture_user("admin", platform="grandmaster")
        self.target = fixture_user("target")
        # New attempt tests do not reuse or overwrite a legacy pending request.
        self.gateway = Gateway.query.filter_by(code="RFD").one()
        self.node = NeoNode.query.filter_by(code="scorpion").one()
        db.session.commit()
        login_user(self.admin)

    def tearDown(self):
        db.session.rollback()
        logout_user()
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def role(self, scope=None, role="operator", active=True, version=0, target=None):
        return stage_role((target or self.target).id, scope or Scope("node", "neogateway", self.gateway.id, self.node.id),
                          role, active, "Reviewed synthetic scope", expected_version=version,
                          expected_session=self.admin.auth_session_version,
                          expected_target_session=(target or self.target).auth_session_version)

    def attempt(self, user=None):
        login_user(user or self.target)
        result = request_access("neostaffing", "Staffing duties", str(uuid4()), expected_session=(user or self.target).auth_session_version)
        db.session.commit()
        return result

    def decide(self, attempt, outcome="approved", **kwargs):
        login_user(self.admin)
        options = dict(expected_version=attempt.version, expected_session=self.admin.auth_session_version,
                       expected_target_session=self.target.auth_session_version,
                       expected_entitlement=entitlement_fingerprint(self.target.id, attempt.app_code))
        options.update(kwargs)
        return decide_access(attempt.id, outcome, "Reviewed request", **options)

    def test_scope_identity_rejects_aliases_and_cross_scope_fields(self):
        valid = [Scope("neoapps"), Scope("application", "neostaffing"), Scope("gateway", "neogateway", 1), Scope("node", "neogateway", 1, 2)]
        self.assertEqual([s.key for s in valid], ["neoapps", "app:neostaffing", "gateway:1", "node:1:2"])
        invalid = [("NeoApps",), ("application", ["neostaffing"]), ("application", "motherbrain"), ("application", "neostaffing", 1),
                   ("node", "neostaffing", 1, 2), ("node", "neogateway", True, 2), ("gateway", "neogateway", 0)]
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ValueError):
                Scope(*args)

    def test_application_administration_is_conjunction_not_maximum(self):
        base = Authority(1, 1, True, False, (), frozenset({"neostaffing", "neogateway"}), frozenset({1}), frozenset({2}))
        app = Scope("application", "neostaffing")
        for platform in ("watcher", "operator", "simulator", "master", "grandmaster"):
            for local in ("watcher", "operator", "simulator", "master", "grandmaster"):
                with self.subTest(platform=platform, local=local):
                    authority = replace(base, roles=(("neoapps", platform), (app.key, local)))
                    self.assertEqual(authority.can_administer(app), platform == "grandmaster" or platform == "master" and local == "grandmaster")
                    self.assertFalse(replace(authority, restricted=True).can_administer(app))
                    self.assertFalse(replace(authority, active=False).can_administer(app))
                    if platform != "grandmaster":
                        self.assertFalse(authority.can_administer(Scope("application", "neogateway")))

    def test_exact_node_gateway_and_entitlement_gates_no_inheritance(self):
        scope = Scope("node", "neogateway", 1, 2)
        base = Authority(1, 1, True, False, ((scope.key, "master"), ("app:neogateway", "grandmaster")),
                         frozenset({"neogateway"}), frozenset({1}), frozenset({2, 3}))
        self.assertEqual(base.role(scope), "master")
        self.assertIsNone(base.role(Scope("node", "neogateway", 1, 3)))
        self.assertIsNone(base.role(Scope("node", "neogateway", 2, 2)))
        self.assertIsNone(replace(base, applications=frozenset()).role(scope))
        self.assertIsNone(replace(base, gateways=frozenset()).role(scope))
        self.assertIsNone(replace(base, nodes=frozenset()).role(scope))
        self.assertFalse(base.can_administer(Scope("neoapps")))

    def test_staging_is_versioned_audited_and_never_changes_live_roles_sessions(self):
        before = read_authority(self.target.id)
        row = self.role()
        db.session.commit()
        self.assertEqual(row.version, 1)
        self.assertEqual(read_authority(self.target.id), before)
        self.assertEqual(read_authority(self.target.id, proposed=True).role(Scope("node", "neogateway", self.gateway.id, self.node.id)), "operator")
        receipt = db.session.get(AccountSecurityEvent, row.security_event_id)
        self.assertEqual(receipt.actor_user_id, self.admin.id)
        self.assertFalse(receipt.details["effective_access_changed"])
        self.assertEqual(receipt.details["before"], None)
        self.role(role="simulator", version=1)
        db.session.commit()
        self.assertEqual(row.version, 2)
        with self.assertRaises(AccountConflict):
            self.role(version=1)
        db.session.rollback()
        self.assertEqual(row.role, "simulator")
        self.assertEqual(AccountSecurityEvent.query.filter_by(action="scoped_role_staged").count(), 2)

    def test_missing_new_node_is_not_silently_granted_and_projection_is_read_only(self):
        fresh = NeoNode(code="futurefixture", name="Future fixture", is_active=True)
        db.session.add(fresh)
        db.session.commit()
        scope = Scope("node", "neogateway", self.gateway.id, fresh.id)
        count = GatewayNodeRole.query.count()
        before = AccountScopedRole.query.count()
        for _ in range(2):
            self.assertIsNone(read_authority(self.target.id, proposed=True).role(scope))
            differences = compare_authority(self.target.id, [scope])
            self.assertEqual(differences[0]["current_role"], "watcher")
            self.assertEqual(differences[0]["proposed_role"], None)
        self.assertEqual(GatewayNodeRole.query.count(), count)
        self.assertEqual(AccountScopedRole.query.count(), before)
        self.assertFalse(db.session.new or db.session.dirty or db.session.deleted)

    def test_current_adapter_matches_canonical_node_projection(self):
        for user in (self.admin, self.target):
            roles = prime_user_node_roles_for_request(user, "RFD", ["scorpion", "sektor", "motherbrain"])
            for code, expected in roles.items():
                node = NeoNode.query.filter_by(code=code).one()
                self.assertEqual(read_authority(user.id).role(Scope("node", "neogateway", self.gateway.id, node.id)), expected)

    def test_reviewed_synthetic_mapping_matches_without_runtime_activation(self):
        scopes = [Scope("neoapps"), Scope("application", "neogateway")]
        scopes += [Scope("node", "neogateway", self.gateway.id, n.id) for n in NeoNode.query.filter_by(is_active=True).all()]
        before = read_authority(self.target.id)
        for scope in scopes:
            receipt = AccountSecurityEvent(actor_user_id=self.admin.id, subject_user_id=self.target.id,
                action="synthetic_mapping_fixture", details={"source": "controlled fixture only"})
            db.session.add(receipt)
            db.session.flush()
            db.session.add(AccountScopedRole(user_id=self.target.id, kind=scope.kind, scope_key=scope.key,
                app_code=scope.app_code, gateway_id=scope.gateway_id, node_id=scope.node_id,
                role=before.role(scope), security_event_id=receipt.id))
        db.session.commit()
        self.assertEqual(compare_authority(self.target.id, scopes), ())
        membership = GatewayMembership.query.filter_by(user_id=self.target.id, gateway_id=self.gateway.id).one()
        membership.is_active = False
        db.session.commit()
        for scope in scopes[2:]:
            self.assertIsNone(read_authority(self.target.id, proposed=True).role(scope))
        self.assertEqual(compare_authority(self.target.id, scopes), ())

    def test_inactive_higher_role_cannot_be_demoted_by_node_master(self):
        self.admin = fixture_user("masterforinactive", nodes={"scorpion": "master"})
        membership = GatewayMembership.query.filter_by(user_id=self.target.id, gateway_id=self.gateway.id).one()
        grant = GatewayNodeRole.query.filter_by(gateway_membership_id=membership.id, node_id=self.node.id).one()
        grant.role, grant.is_active = "grandmaster", False
        db.session.commit()
        login_user(self.admin)
        with self.assertRaises(ValueError):
            self.role(role="operator")
        self.assertEqual(AccountScopedRole.query.count(), 0)
        self.assertEqual((grant.role, grant.is_active), ("grandmaster", False))

    def test_denial_reapplication_and_idempotent_request_preserve_history(self):
        attempt = self.attempt()
        login_user(self.target)
        retry = request_access(attempt.app_code, attempt.reason, attempt.request_key, expected_session=self.target.auth_session_version)
        self.assertEqual(retry.id, attempt.id)
        with self.assertRaises(AccountConflict):
            request_access(attempt.app_code, "Other reason", attempt.request_key, expected_session=self.target.auth_session_version)
        db.session.rollback()
        self.decide(attempt, "denied")
        db.session.commit()
        second = self.attempt()
        self.assertNotEqual(second.id, attempt.id)
        self.assertEqual(attempt.state, "denied")
        self.assertEqual(AccountAccessDecision.query.count(), 1)
        login_user(self.target)
        with self.assertRaises(AccountConflict):
            request_access("neostaffing", "Another", str(uuid4()), expected_session=self.target.auth_session_version)

    def test_approval_is_not_entitlement_or_role_migration(self):
        original = read_authority(self.target.id)
        attempt = self.attempt()
        self.decide(attempt)
        db.session.commit()
        self.assertEqual(attempt.state, "approved")
        self.assertEqual(read_authority(self.target.id), original)
        self.assertIsNone(PortalAppAccess.query.filter_by(user_id=self.target.id, app_code="neostaffing").first())
        with self.assertRaises(FoundationNotActivated):
            fulfill_access_request(attempt.id)
        with self.assertRaises(AccountConflict):
            self.decide(attempt)
        db.session.rollback()
        self.assertEqual(AccountAccessDecision.query.count(), 1)

    def test_stale_entitlement_legacy_writer_and_session_rejected(self):
        attempt = self.attempt()
        fingerprint = entitlement_fingerprint(self.target.id, "neostaffing")
        db.session.add(PortalAppAccess(user_id=self.target.id, app_code="neostaffing", status="denied", role="watcher"))
        db.session.commit()
        with self.assertRaises(AccountConflict):
            self.decide(attempt, expected_entitlement=fingerprint)
        db.session.rollback()
        with self.assertRaises(AccountConflict):
            self.decide(attempt, expected_target_session=0)
        db.session.rollback()
        with self.assertRaises(AccountConflict):
            self.decide(attempt, expected_session=0)
        db.session.rollback()
        self.target.is_active = False
        db.session.commit()
        with self.assertRaises(ValueError):
            self.decide(attempt)
        db.session.rollback()
        self.assertEqual(attempt.state, "pending")
        self.assertEqual(AccountAccessDecision.query.count(), 0)

    def test_self_approval_foreign_authority_and_protected_target_rejected(self):
        attempt = self.attempt()
        for actor in (self.target, fixture_user("nodeonly", nodes={"motherbrain": "grandmaster"})):
            db.session.commit()
            login_user(actor)
            with self.assertRaises(ValueError):
                decide_access(attempt.id, "approved", "Forged request", expected_version=1,
                              expected_session=actor.auth_session_version, expected_target_session=self.target.auth_session_version,
                              expected_entitlement=entitlement_fingerprint(self.target.id, "neostaffing"))
            db.session.rollback()
        self.assertEqual(AccountAccessDecision.query.count(), 0)

    def test_master_can_stage_only_lower_roles_in_own_node(self):
        actor = fixture_user("nodemaster", nodes={"scorpion": "master"})
        db.session.commit()
        self.admin = actor
        login_user(actor)
        self.role(role="simulator")
        db.session.commit()
        for scope, role in ((Scope("neoapps"), "operator"), (Scope("application", "neostaffing"), "operator"),
                            (Scope("node", "neogateway", self.gateway.id, self.node.id), "master"),
                            (Scope("node", "neogateway", self.gateway.id, self.node.id), "grandmaster")):
            with self.subTest(scope=scope, role=role), self.assertRaises(ValueError):
                self.role(scope=scope, role=role, version=1)
            db.session.rollback()
        with self.assertRaises(ValueError):
            self.role(target=actor)
        db.session.rollback()

    def test_platform_grandmaster_commissioning_and_removal_fail_closed(self):
        for scope in (Scope("neoapps"), Scope("application", "neostaffing")):
            with self.assertRaises(ValueError):
                self.role(scope=scope, role="grandmaster")
            db.session.rollback()
        self.assertEqual(AccountScopedRole.query.count(), 0)
        self.assertEqual(self.admin.role, "grandmaster")
        other = fixture_user("otherplatformadmin", platform="grandmaster")
        db.session.commit()
        with self.assertRaises(ValueError):
            self.role(scope=Scope("neoapps"), role="watcher", active=False, target=other)
        self.assertEqual(other.role, "grandmaster")

    def test_database_scope_checks_and_one_open_request_constraint(self):
        receipt = AccountSecurityEvent(actor_user_id=self.admin.id, subject_user_id=self.target.id, action="fixture", details={})
        db.session.add(receipt)
        db.session.commit()
        for values in (dict(kind="application", scope_key="app:neostaffing", app_code=None),
                       dict(kind="node", scope_key="neoapps", app_code="neogateway", gateway_id=self.gateway.id, node_id=self.node.id)):
            with self.subTest(values=values), self.assertRaises(IntegrityError):
                db.session.add(AccountScopedRole(user_id=self.target.id, role="operator", version=1, security_event_id=receipt.id, **values))
                db.session.flush()
            db.session.rollback()
        attempt = self.attempt()
        with self.assertRaises(IntegrityError):
            db.session.add(AccountAccessRequest(user_id=self.target.id, app_code=attempt.app_code, request_key=str(uuid4()), reason="duplicate"))
            db.session.flush()
        db.session.rollback()

    def test_bulk_history_tampering_rejected_and_schema_install_idempotent(self):
        attempt = self.attempt()
        self.decide(attempt)
        db.session.commit()
        for _ in range(2):
            ensure_account_history_guards(db.session.connection())
            db.session.commit()
        for table in ("account_security_events", "account_access_decisions"):
            for action in (f"UPDATE {table} SET id=id", f"DELETE FROM {table}"):
                with self.subTest(action=action), self.assertRaises(DBAPIError):
                    db.session.execute(text(action))
                db.session.rollback()
        self.assertEqual(AccountAccessDecision.query.count(), 1)

    def test_partial_mutation_and_receipt_rollback_as_one_transaction(self):
        attempt = self.attempt()
        self.decide(attempt)
        db.session.rollback()
        self.assertEqual(attempt.state, "pending")
        self.assertEqual(attempt.version, 1)
        self.assertEqual(AccountAccessDecision.query.count(), 0)
        self.assertEqual(AccountSecurityEvent.query.filter_by(action="access_request_decided").count(), 0)

    def test_no_live_enforcement_cutover_or_ui_routes(self):
        before = user_can(self.target, "neoscorpion.fuel_dispatch.edit")
        self.role(role="master")
        db.session.commit()
        self.assertEqual(user_can(self.target, "neoscorpion.fuel_dispatch.edit"), before)
        self.assertFalse(any("account_foundation" in rule.endpoint for rule in self.app.url_map.iter_rules()))
