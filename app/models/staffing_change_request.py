from datetime import datetime

from app.extensions import db


STAFFING_CHANGE_REQUEST_STATUSES = ("pending", "completed")


class StaffingChangeRequest(db.Model):
    __tablename__ = "staffing_change_requests"
    __table_args__ = (
        db.CheckConstraint(
            "status IN ('pending', 'completed')",
            name="ck_staffing_change_requests_status",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_people.id"),
        nullable=False,
        index=True,
    )
    submitted_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    submitted_by_person_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_people.id"),
        nullable=True,
        index=True,
    )
    source_work_area_unit_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_units.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    destination_work_area_unit_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_units.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    routed_approver_person_ids_json = db.Column(
        db.Text,
        nullable=False,
        default="[]",
    )
    unassigned_approval = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        index=True,
    )
    request_note = db.Column(db.Text, nullable=True)
    status = db.Column(
        db.String(24),
        nullable=False,
        default="pending",
        index=True,
    )
    submitted_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        index=True,
    )
    completed_at = db.Column(db.DateTime, nullable=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    person = db.relationship(
        "StaffingPerson",
        foreign_keys=[person_id],
    )
    submitted_by_person = db.relationship(
        "StaffingPerson",
        foreign_keys=[submitted_by_person_id],
    )
    submitted_by_user = db.relationship("User", foreign_keys=[submitted_by_user_id])
    source_work_area = db.relationship(
        "StaffingUnit",
        foreign_keys=[source_work_area_unit_id],
    )
    destination_work_area = db.relationship(
        "StaffingUnit",
        foreign_keys=[destination_work_area_unit_id],
    )
    items = db.relationship(
        "StaffingChangeRequestItem",
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="StaffingChangeRequestItem.id",
    )
    events = db.relationship(
        "StaffingChangeRequestEvent",
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="StaffingChangeRequestEvent.id",
    )
