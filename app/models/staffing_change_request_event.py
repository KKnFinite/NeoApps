from datetime import datetime

from app.extensions import db


class StaffingChangeRequestEvent(db.Model):
    __tablename__ = "staffing_change_request_events"

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_change_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_change_request_items.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    actor_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
        index=True,
    )
    event_type = db.Column(db.String(40), nullable=False, index=True)
    from_status = db.Column(db.String(24), nullable=True)
    to_status = db.Column(db.String(24), nullable=True)
    reason = db.Column(db.Text, nullable=True)
    details_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        index=True,
    )

    request = db.relationship("StaffingChangeRequest", back_populates="events")
    item = db.relationship("StaffingChangeRequestItem", back_populates="events")
    actor_user = db.relationship("User", foreign_keys=[actor_user_id])
