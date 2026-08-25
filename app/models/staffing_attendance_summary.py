from datetime import datetime

from app.extensions import db


STAFFING_ATTENDANCE_SUMMARY_SCOPE_TYPES = ("operation", "department")


class StaffingAttendanceSummary(db.Model):
    """Retained Operation/Department payroll and worked totals for one sort."""

    __tablename__ = "staffing_attendance_summaries"
    __table_args__ = (
        db.CheckConstraint(
            "scope_type IN ('operation', 'department')",
            name="ck_staffing_attendance_summaries_scope_type",
        ),
        db.CheckConstraint(
            "on_payroll_count >= 0 AND worked_count >= 0",
            name="ck_staffing_attendance_summaries_counts_nonnegative",
        ),
        db.UniqueConstraint(
            "sort_date_operation_id",
            "scope_type",
            "scope_unit_id",
            name="uq_staffing_attendance_summaries_operation_scope",
        ),
        db.Index(
            "ix_staffing_attendance_summaries_retention",
            "attendance_date",
            "id",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_operations.id"),
        nullable=False,
        index=True,
    )
    attendance_date = db.Column(db.Date, nullable=False, index=True)
    scope_type = db.Column(db.String(32), nullable=False, index=True)
    scope_unit_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_units.id"),
        nullable=False,
        index=True,
    )
    on_payroll_count = db.Column(db.Integer, nullable=False, default=0)
    worked_count = db.Column(db.Integer, nullable=False, default=0)
    finalized_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    finalized_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    updated_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )

    sort_date_operation = db.relationship("SortDateOperation")
    scope_unit = db.relationship("StaffingUnit")
    finalized_by_user = db.relationship(
        "User",
        foreign_keys=[finalized_by_user_id],
    )
    updated_by_user = db.relationship(
        "User",
        foreign_keys=[updated_by_user_id],
    )
