"""Dormant control-plane records. These are NOT live authorization sources."""
from datetime import datetime

from sqlalchemy import event, text

from app.extensions import db


ROLES_SQL = "('watcher','operator','simulator','master','grandmaster')"
APPS_SQL = "('neogateway','neostaffing','neobid')"


class AccountScopedRole(db.Model):
    __tablename__ = "account_scoped_roles"
    __table_args__ = (
        db.UniqueConstraint("user_id", "scope_key", name="uq_account_scoped_role"),
        db.CheckConstraint("role IN " + ROLES_SQL, name="ck_account_scoped_role_rank"),
        db.CheckConstraint("version > 0", name="ck_account_scoped_role_version"),
        db.CheckConstraint(
            "(kind='neoapps' AND scope_key='neoapps' AND app_code IS NULL AND gateway_id IS NULL AND node_id IS NULL) OR "
            "(kind='application' AND app_code IS NOT NULL AND app_code IN " + APPS_SQL + " AND scope_key='app:' || app_code AND gateway_id IS NULL AND node_id IS NULL) OR "
            "(kind='gateway' AND app_code IS NOT NULL AND app_code='neogateway' AND gateway_id IS NOT NULL AND node_id IS NULL AND scope_key='gateway:' || CAST(gateway_id AS TEXT)) OR "
            "(kind='node' AND app_code IS NOT NULL AND app_code='neogateway' AND gateway_id IS NOT NULL AND node_id IS NOT NULL AND scope_key='node:' || CAST(gateway_id AS TEXT) || ':' || CAST(node_id AS TEXT))",
            name="ck_account_scoped_role_scope",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    kind = db.Column(db.String(16), nullable=False)
    scope_key = db.Column(db.String(100), nullable=False)
    app_code = db.Column(db.String(32))
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"))
    node_id = db.Column(db.Integer, db.ForeignKey("neo_nodes.id"))
    role = db.Column(db.String(32), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    # Every staged change has an immutable receipt. No role is seeded on GET.
    security_event_id = db.Column(db.Integer, db.ForeignKey("account_security_events.id"), nullable=False)


class AccountAccessRequest(db.Model):
    __tablename__ = "account_access_requests"
    __table_args__ = (
        db.UniqueConstraint("user_id", "request_key", name="uq_account_access_request_retry"),
        db.Index("uq_account_access_request_open", "user_id", "app_code", unique=True,
                 sqlite_where=text("state='pending'"), postgresql_where=text("state='pending'")),
        db.CheckConstraint("app_code IN " + APPS_SQL, name="ck_account_access_request_app"),
        db.CheckConstraint("state IN ('pending','approved','denied','withdrawn')", name="ck_account_access_request_state"),
        db.CheckConstraint("version > 0", name="ck_account_access_request_version"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    app_code = db.Column(db.String(32), nullable=False)
    request_key = db.Column(db.String(36), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    state = db.Column(db.String(16), nullable=False, default="pending")
    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class AccountAccessDecision(db.Model):
    __tablename__ = "account_access_decisions"
    __table_args__ = (
        db.CheckConstraint("outcome IN ('approved','denied','withdrawn')", name="ck_account_access_decision_outcome"),
    )

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey("account_access_requests.id"), nullable=False, unique=True)
    outcome = db.Column(db.String(16), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    security_event_id = db.Column(db.Integer, db.ForeignKey("account_security_events.id"), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


@event.listens_for(AccountAccessDecision, "before_update")
@event.listens_for(AccountAccessDecision, "before_delete")
def _immutable_decision(*_args):
    raise ValueError("Account access decisions are immutable.")
