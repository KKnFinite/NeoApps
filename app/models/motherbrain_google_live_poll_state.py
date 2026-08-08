from datetime import datetime

from app.extensions import db


class MotherBrainGoogleLivePollState(db.Model):
    """Database-backed lease state for a single Google MotherBrain poll scope."""

    __tablename__ = "motherbrain_google_live_poll_states"
    __table_args__ = (
        db.UniqueConstraint(
            "gateway_id",
            "sort_name",
            "sort_date",
            name="uq_motherbrain_google_live_poll_gateway_sort_date",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(
        db.Integer,
        db.ForeignKey("gateways.id"),
        nullable=False,
        index=True,
    )
    sort_name = db.Column(db.String(32), nullable=False, index=True)
    sort_date = db.Column(db.Date, nullable=False, index=True)
    last_attempt_at_utc = db.Column(db.DateTime, nullable=True)
    last_success_at_utc = db.Column(db.DateTime, nullable=True)
    last_error = db.Column(db.String(255), nullable=True)
    lease_expires_at_utc = db.Column(db.DateTime, nullable=True)
    lease_token = db.Column(db.String(64), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gateway = db.relationship("Gateway")
