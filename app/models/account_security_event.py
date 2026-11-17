"""Security changes recorded in the same transaction as the account change."""
from datetime import datetime

from sqlalchemy import event

from app.extensions import db


class AccountSecurityEvent(db.Model):
    __tablename__ = "account_security_events"

    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    subject_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    action = db.Column(db.String(80), nullable=False)
    details = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


@event.listens_for(AccountSecurityEvent, "before_update")
@event.listens_for(AccountSecurityEvent, "before_delete")
def _immutable_security_event(*_args):
    raise ValueError("Account security history is immutable.")
