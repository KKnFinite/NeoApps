from datetime import datetime

from app.extensions import db


class SortDateParkingAssignment(db.Model):
    __tablename__ = "sort_date_parking_assignments"
    __table_args__ = (
        db.CheckConstraint(
            "lane_number IS NULL OR lane_number IN (1, 2)",
            name="ck_sort_date_parking_assignments_lane",
        ),
        db.UniqueConstraint(
            "sort_date_operation_id",
            "tail_number",
            name="uq_sort_date_parking_assignment_tail",
        ),
        db.UniqueConstraint(
            "sort_date_operation_id",
            "ramp_code",
            "position_code",
            "lane_number",
            name="uq_sort_date_parking_assignment_lane",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_operations.id"),
        nullable=False,
        index=True,
    )
    tail_number = db.Column(db.String(32), nullable=False, index=True)
    ramp_code = db.Column(db.String(16), nullable=True, index=True)
    position_code = db.Column(db.String(16), nullable=True, index=True)
    lane_number = db.Column(db.Integer, nullable=True, index=True)
    is_hot = db.Column(db.Boolean, nullable=False, default=False)
    note = db.Column(db.Text, nullable=False, default="")
    assigned_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    assigned_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    sort_date_operation = db.relationship("SortDateOperation")
    assigned_by = db.relationship("User")
