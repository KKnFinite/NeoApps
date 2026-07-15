from datetime import datetime

from app.extensions import db


class AuthRateLimitState(db.Model):
    """Persistent login and password-reset abuse-control state."""

    __tablename__ = "auth_rate_limit_states"
    __table_args__ = (
        db.UniqueConstraint(
            "action",
            "subject_type",
            "subject_digest",
            name="uq_auth_rate_limit_state_subject",
        ),
        db.Index(
            "ix_auth_rate_limit_states_action_subject_updated",
            "action",
            "subject_type",
            "updated_at",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(32), nullable=False, index=True)
    subject_type = db.Column(db.String(16), nullable=False, index=True)
    subject_digest = db.Column(db.String(64), nullable=False, index=True)
    window_started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    attempt_count = db.Column(db.Integer, nullable=False, default=0)
    blocked_until = db.Column(db.DateTime, nullable=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
