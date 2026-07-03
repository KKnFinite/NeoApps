from datetime import datetime

from app.extensions import db


STAFFING_DAILY_ATTENDANCE_STATUSES = (
    "here",
    "call_in",
    "no_call",
    "vacation",
    "optional_day",
    "anniversary_day",
    "funeral",
    "jury",
    "int_fmla",
    "disability",
    "comp",
    "military",
    "cleared",
)


class StaffingDailyAttendance(db.Model):
    __tablename__ = "staffing_daily_attendance"
    __table_args__ = (
        db.CheckConstraint(
            "status IN ('here', 'call_in', 'no_call', 'vacation', 'optional_day', "
            "'anniversary_day', 'funeral', 'jury', 'int_fmla', 'disability', "
            "'comp', 'military', 'cleared')",
            name="ck_staffing_daily_attendance_status",
        ),
        db.UniqueConstraint(
            "person_id",
            "attendance_date",
            "sort_unit_id",
            name="uq_staffing_daily_attendance_person_date_sort",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    attendance_date = db.Column(db.Date, nullable=False, index=True)
    sort_unit_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_units.id"),
        nullable=False,
        index=True,
    )
    person_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_people.id"),
        nullable=False,
        index=True,
    )
    work_area_unit_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_units.id"),
        nullable=True,
        index=True,
    )
    status = db.Column(db.String(32), nullable=False, index=True)
    note = db.Column(db.Text, nullable=True)
    recorded_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    recorded_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    person = db.relationship("StaffingPerson", back_populates="daily_attendance_records")
    sort = db.relationship("StaffingUnit", foreign_keys=[sort_unit_id])
    work_area = db.relationship("StaffingUnit", foreign_keys=[work_area_unit_id])
    recorded_by_user = db.relationship("User", foreign_keys=[recorded_by_user_id])
    updated_by_user = db.relationship("User", foreign_keys=[updated_by_user_id])
