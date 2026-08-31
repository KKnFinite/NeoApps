from datetime import datetime

from app.extensions import db


class NeoSubZeroCalloutAssignment(db.Model):
    """Explicit, current-sort membership in the SubZero callout pool."""

    __tablename__ = "neosubzero_callout_assignments"
    __table_args__ = (
        db.UniqueConstraint(
            "sort_date_operation_id",
            "person_id",
            name="uq_neosubzero_callout_operation_person",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_operations.id"),
        nullable=False,
        index=True,
    )
    person_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_people.id"),
        nullable=False,
        index=True,
    )
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    selected_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    selected_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )
    removed_at = db.Column(db.DateTime, nullable=True)
    removed_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )
    removal_reason = db.Column(db.String(32), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    operation = db.relationship("SortDateOperation")
    person = db.relationship("StaffingPerson")
    selected_by_user = db.relationship("User", foreign_keys=[selected_by_user_id])
    removed_by_user = db.relationship("User", foreign_keys=[removed_by_user_id])
