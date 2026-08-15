from datetime import datetime

from sqlalchemy import text

from app.extensions import db


STAFFING_CHANGE_REQUEST_FIELDS = (
    "first_name",
    "last_name",
    "seniority_date",
    "employee_status",
    "classification",
    "work_area_unit_id",
)
STAFFING_CHANGE_REQUEST_ITEM_STATUSES = (
    "pending",
    "approved",
    "denied",
    "withdrawn",
    "superseded",
)


class StaffingChangeRequestItem(db.Model):
    __tablename__ = "staffing_change_request_items"
    __table_args__ = (
        db.CheckConstraint(
            "field_name IN ('first_name', 'last_name', 'seniority_date', "
            "'employee_status', 'classification', 'work_area_unit_id')",
            name="ck_staffing_change_request_items_field",
        ),
        db.CheckConstraint(
            "status IN ('pending', 'approved', 'denied', 'withdrawn', 'superseded')",
            name="ck_staffing_change_request_items_status",
        ),
        db.Index(
            "uq_staffing_change_request_items_pending_field",
            "person_id",
            "field_name",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_change_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    person_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_people.id"),
        nullable=False,
        index=True,
    )
    field_name = db.Column(db.String(40), nullable=False, index=True)
    original_value_json = db.Column(db.Text, nullable=True)
    requested_value_json = db.Column(db.Text, nullable=True)
    status = db.Column(
        db.String(24),
        nullable=False,
        default="pending",
        index=True,
    )
    decision_reason = db.Column(db.Text, nullable=True)
    decided_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
        index=True,
    )
    decided_at = db.Column(db.DateTime, nullable=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    request = db.relationship("StaffingChangeRequest", back_populates="items")
    person = db.relationship("StaffingPerson", foreign_keys=[person_id])
    decided_by_user = db.relationship("User", foreign_keys=[decided_by_user_id])
    events = db.relationship(
        "StaffingChangeRequestEvent",
        back_populates="item",
        cascade="all, delete-orphan",
        order_by="StaffingChangeRequestEvent.id",
    )
