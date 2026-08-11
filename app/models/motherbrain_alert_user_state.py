from datetime import datetime

from app.extensions import db


class MotherBrainAlertUserState(db.Model):
    __tablename__ = "motherbrain_alert_user_states"
    __table_args__ = (
        db.UniqueConstraint(
            "alert_id",
            "user_id",
            name="uq_motherbrain_alert_user_state_alert_user",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    alert_id = db.Column(
        db.Integer,
        db.ForeignKey("motherbrain_alerts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    read_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
