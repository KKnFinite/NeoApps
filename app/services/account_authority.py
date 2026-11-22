"""Explicit canonical scope projection and isolated candidate comparison.

The deployed permission_rules/user_can engine remains authoritative. This module
reads canonical adapters without calling getters which can seed missing grants.
Staged roles are evaluated separately; old and new rights are never unioned.
"""
from dataclasses import dataclass

from app.extensions import db
from app.models import AccountScopedRole, Gateway, GatewayMembership, GatewayNodeRole, NeoNode, PortalAppAccess, User
from app.models.neoscorpion_vendor import NeoScorpionVendorAccount
from app.models.user import ROLE_LEVELS


APP_CODES = frozenset({"neogateway", "neostaffing", "neobid"})


@dataclass(frozen=True)
class Scope:
    kind: str
    app_code: str | None = None
    gateway_id: int | None = None
    node_id: int | None = None

    def __post_init__(self):
        positive = lambda value: type(value) is int and value > 0
        valid = (
            self.kind == "neoapps" and self.app_code is None and self.gateway_id is None and self.node_id is None
            or self.kind == "application" and isinstance(self.app_code, str) and self.app_code in APP_CODES and self.gateway_id is None and self.node_id is None
            or self.kind == "gateway" and self.app_code == "neogateway" and positive(self.gateway_id) and self.node_id is None
            or self.kind == "node" and self.app_code == "neogateway" and positive(self.gateway_id) and positive(self.node_id)
        )
        if not valid:
            raise ValueError("Invalid explicit account scope.")

    @property
    def key(self):
        if self.kind == "neoapps":
            return "neoapps"
        if self.kind == "application":
            return "app:" + self.app_code
        if self.kind == "gateway":
            return f"gateway:{self.gateway_id}"
        return f"node:{self.gateway_id}:{self.node_id}"


@dataclass(frozen=True)
class Authority:
    user_id: int
    session_version: int
    active: bool
    restricted: bool
    # Tuples, not ORM objects or long-lived authorization caches.
    roles: tuple[tuple[str, str], ...]
    applications: frozenset[str]
    gateways: frozenset[int]
    nodes: frozenset[int]

    def role(self, scope):
        if not self.active or self.restricted:
            return None
        if scope.kind != "neoapps" and scope.app_code not in self.applications:
            return None
        if scope.kind in {"gateway", "node"} and scope.gateway_id not in self.gateways:
            return None
        if scope.kind == "node" and scope.node_id not in self.nodes:
            return None
        return dict(self.roles).get(scope.key)

    def can_administer(self, scope):
        """Parent oversight is a capability, not an inherited operational role."""
        if self.role(Scope("neoapps")) == "grandmaster":
            return True
        if scope.kind == "application":
            return (self.role(Scope("neoapps")) == "master"
                    and self.role(scope) == "grandmaster")
        if scope.kind in {"gateway", "node"}:
            if scope.gateway_id not in self.gateways or 'neogateway' not in self.applications:
                return False
            if scope.kind=='node' and scope.node_id not in self.nodes:
                return False
            parent=self.role(Scope('application','neogateway'))=='grandmaster'
            gateway=self.role(Scope('gateway','neogateway',scope.gateway_id))=='grandmaster'
            return parent or gateway or (scope.kind=='node' and self.role(scope)=='master')
        return False


def read_authority(user_id, *, proposed=False):
    """Read-only bounded adapter. No seeding, autoflush, mutations or global cache."""
    with db.session.no_autoflush:
        from app.services.account_scope_runtime import scoped_active
        explicit=scoped_active()
        user = db.session.query(User.id, User.role, User.is_active, User.auth_session_version,
            NeoScorpionVendorAccount.user_id.label("vendor_id")).outerjoin(
                NeoScorpionVendorAccount, NeoScorpionVendorAccount.user_id == User.id
            ).filter(User.id == user_id).one()
        restricted = user.vendor_id is not None
        apps = db.session.query(PortalAppAccess.app_code, PortalAppAccess.status, PortalAppAccess.is_active, PortalAppAccess.role).filter_by(user_id=user_id).all()
        memberships = db.session.query(GatewayMembership.id, GatewayMembership.gateway_id, GatewayMembership.status,
                                       GatewayMembership.is_active, Gateway.is_active.label("gateway_active"))\
            .join(Gateway, Gateway.id == GatewayMembership.gateway_id).filter(GatewayMembership.user_id == user_id).all()
        nodes = db.session.query(NeoNode.id, NeoNode.is_active).all()
        grants = db.session.query(GatewayNodeRole.node_id, GatewayNodeRole.role, GatewayNodeRole.is_active,
                                  GatewayMembership.gateway_id).join(GatewayMembership).filter(GatewayMembership.user_id == user_id).all()
        entitled_apps = {r.app_code for r in apps if r.status == "approved" and r.is_active}
        gateways = {m.gateway_id for m in memberships if m.status == "approved" and m.is_active and m.gateway_active}
        # Preserve the live adapter's documented legacy app fallback ONLY on the
        # current side of comparison. An explicit denial/tombstone always wins.
        app_roles = {r.app_code: r.role for r in apps if r.app_code in entitled_apps}
        if not explicit and not any(r.app_code == "neogateway" for r in apps) and gateways:
            entitled_apps.add("neogateway")
            app_roles["neogateway"] = user.role
        roles = {"neoapps": user.role}
        roles.update({"app:" + code: role for code, role in app_roles.items()})
        grant_map = {(r.gateway_id, r.node_id): r for r in grants}
        if explicit:
            roles.update({r.scope_key:r.role for r in db.session.query(AccountScopedRole.scope_key,AccountScopedRole.role)
                          .filter_by(user_id=user_id,kind='gateway',is_active=True).all()})
        for gateway_id in gateways:
            # Membership has no rank column. Do not invent a gateway admin rank
            # from the app role; its future mapping requires explicit review.
            for node in nodes:
                existing = grant_map.get((gateway_id, node.id))
                role = existing.role if existing and existing.is_active else None if existing or explicit else app_roles.get("neogateway")
                if role:
                    roles[f"node:{gateway_id}:{node.id}"] = role
        if proposed and not explicit:
            roles = {r.scope_key: r.role for r in db.session.query(AccountScopedRole.scope_key, AccountScopedRole.role)
                     .filter_by(user_id=user_id, is_active=True).all()}
        return Authority(user.id, user.auth_session_version, user.is_active, restricted,
                         tuple(sorted(roles.items())), frozenset(entitled_apps), frozenset(gateways),
                         frozenset(n.id for n in nodes if n.is_active))


def compare_authority(user_id, scopes):
    """Return differences for explicit review; never authorizes a request."""
    before, after = read_authority(user_id), read_authority(user_id, proposed=True)
    return tuple({"scope": scope.key, "current_role": before.role(scope), "proposed_role": after.role(scope),
                  "current_admin": before.can_administer(scope), "proposed_admin": after.can_administer(scope)}
                 for scope in scopes if (before.role(scope), before.can_administer(scope))
                 != (after.role(scope), after.can_administer(scope)))
