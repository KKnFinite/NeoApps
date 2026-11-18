"""Atomic authorization cutover marker; reads never initialize this row."""
from datetime import datetime
from app.extensions import db


class AccountAuthorizationState(db.Model):
    __tablename__ = 'account_authorization_states'
    __table_args__ = (db.CheckConstraint('id = 1 AND version > 0',name='ck_account_authorization_singleton'),)
    id=db.Column(db.Integer,primary_key=True)
    active=db.Column(db.Boolean,nullable=False,default=False)
    version=db.Column(db.Integer,nullable=False,default=1)
    security_event_id=db.Column(db.Integer,db.ForeignKey('account_security_events.id'),nullable=False)
    inventory_fingerprint=db.Column(db.String(64),nullable=False)
    updated_at=db.Column(db.DateTime,nullable=False,default=datetime.utcnow)


class AccountScopedPolicy(db.Model):
    """An exact-scope threshold override, evaluated by the shared engine.

    The existing PermissionRule remains the compatibility baseline. A node
    editor cannot change that baseline for other gateways.
    """
    __tablename__ = 'account_scoped_policies'
    __table_args__ = (
        db.UniqueConstraint('scope_key', 'permission_key', name='uq_account_scoped_policy'),
        db.CheckConstraint("minimum_role IN ('watcher','operator','simulator','master','grandmaster')",
                           name='ck_account_scoped_policy_role'),
        db.CheckConstraint('version > 0', name='ck_account_scoped_policy_version'),
    )
    id = db.Column(db.Integer, primary_key=True)
    scope_key = db.Column(db.String(100), nullable=False)
    permission_key = db.Column(db.String(160), nullable=False)
    minimum_role = db.Column(db.String(32), nullable=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    security_event_id = db.Column(db.Integer, db.ForeignKey('account_security_events.id'), nullable=False)
