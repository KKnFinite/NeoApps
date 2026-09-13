"""Short-lived exact time facts and compact company-wide archive receipts."""
from datetime import datetime

from app.extensions import db


class StaffingTimecardWeek(db.Model):
    __tablename__ = "staffing_timecard_weeks"
    week_start = db.Column(db.Date, primary_key=True)
    version = db.Column(db.Integer, nullable=False, default=0)
    archive_version = db.Column(db.Integer)
    archive_sha256 = db.Column(db.String(64))
    archive_actor_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    downloaded_at = db.Column(db.DateTime)
    purge_after = db.Column(db.DateTime, index=True)
    purged_at = db.Column(db.DateTime)
    purged_slices = db.Column(db.Integer, nullable=False, default=0)


class StaffingTimecardSlice(db.Model):
    __tablename__ = "staffing_timecard_slices"
    __table_args__ = (
        db.UniqueConstraint("person_id", "workday_date", "sort_unit_id", name="uq_staffing_timecard_slice"),
        db.Index("ix_staffing_timecard_week_person", "workday_date", "person_id"),
    )
    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(db.Integer, db.ForeignKey("staffing_people.id"), nullable=False)
    workday_date = db.Column(db.Date, nullable=False)
    sort_unit_id = db.Column(db.Integer, db.ForeignKey("staffing_units.id"), nullable=False)
    sort_date_operation_id = db.Column(db.Integer, db.ForeignKey("sort_date_operations.id"), nullable=False)
    work_area_unit_id = db.Column(db.Integer, db.ForeignKey("staffing_units.id"), nullable=False)
    # Retained source labels survive attendance rollover; organizational changes
    # do not silently rewrite an already downloaded historical labor report.
    context_json = db.Column(db.Text, nullable=False)
    attendance_status = db.Column(db.String(32), nullable=False)
    version = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))


class StaffingTimecardSegment(db.Model):
    __tablename__ = "staffing_timecard_segments"
    __table_args__ = (db.CheckConstraint("end_utc IS NULL OR start_utc IS NULL OR end_utc > start_utc", name="ck_staffing_timecard_segment_order"),)
    id = db.Column(db.Integer, primary_key=True)
    slice_id = db.Column(db.Integer, db.ForeignKey("staffing_timecard_slices.id", ondelete="CASCADE"), nullable=False, index=True)
    # UTC, not rounded hours. NULL endpoint is an explicitly incomplete entry.
    start_utc = db.Column(db.DateTime)
    end_utc = db.Column(db.DateTime)


class StaffingTimecardEdit(db.Model):
    __tablename__ = "staffing_timecard_edits"
    id = db.Column(db.Integer, primary_key=True)
    slice_id = db.Column(db.Integer, db.ForeignKey("staffing_timecard_slices.id", ondelete="CASCADE"), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    recorded_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    snapshot_json = db.Column(db.Text, nullable=False)
    slice = db.relationship("StaffingTimecardSlice")
