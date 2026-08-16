from datetime import datetime

from app.extensions import db


STAFFING_NOTIFICATION_TYPES = (
    "new_request",
    "request_completed",
    "decision_reversed",
    "item_superseded",
    "request_overdue",
)


class StaffingNotification(db.Model):
    __tablename__ = "staffing_notifications"
    __table_args__ = (
        db.CheckConstraint(
            "notification_type IN ('new_request', 'request_completed', "
            "'decision_reversed', 'item_superseded', 'request_overdue')",
            name="ck_staffing_notifications_type",
        ),
        db.UniqueConstraint(
            "dedupe_key",
            name="uq_staffing_notifications_dedupe_key",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    recipient_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    change_request_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_change_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    notification_type = db.Column(db.String(40), nullable=False, index=True)
    message = db.Column(db.Text, nullable=False)
    details_json = db.Column(db.Text, nullable=True)
    dedupe_key = db.Column(db.String(255), nullable=False)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        index=True,
    )
    read_at = db.Column(db.DateTime, nullable=True, index=True)

    recipient_user = db.relationship("User", foreign_keys=[recipient_user_id])
    change_request = db.relationship(
        "StaffingChangeRequest",
        foreign_keys=[change_request_id],
    )
