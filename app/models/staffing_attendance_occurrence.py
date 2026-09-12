"""Compact retained absence facts, independent of daily-detail rollover."""
from datetime import datetime

from app.extensions import db


class StaffingAttendanceOccurrence(db.Model):
    __tablename__ = "staffing_attendance_occurrences"
    __table_args__ = (
        db.UniqueConstraint("person_id", "sort_date_operation_id", name="uq_staffing_occurrence_person_operation"),
        db.CheckConstraint("status IN ('call_in', 'no_call')", name="ck_staffing_occurrence_status"),
        db.Index("ix_staffing_occurrence_retention", "attendance_date", "id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(db.Integer, db.ForeignKey("staffing_people.id", ondelete="CASCADE"), nullable=False)
    # Deliberately not a foreign key to daily attendance: rollover purges those rows.
    sort_date_operation_id = db.Column(db.Integer, db.ForeignKey("sort_date_operations.id"), nullable=False)
    attendance_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(16), nullable=False)
    # No prior history is presumed complete. Future reconciliation may establish
    # it; this foundation never guesses a clean history or blocks an attendance save.
    reconciliation_needed = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    recorded_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
