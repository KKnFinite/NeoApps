from datetime import datetime

from app.extensions import db


STAFFING_OPERATION_SCHEDULE_WEEKDAYS = (
    "sunday",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
)


class StaffingOperationSchedule(db.Model):
    """Effective-dated normal recurring weekdays for one Staffing Operation."""

    __tablename__ = "staffing_operation_schedules"
    __table_args__ = (
        db.CheckConstraint(
            "effective_through IS NULL OR effective_through >= effective_from",
            name="ck_staffing_operation_schedules_effective_range",
        ),
        db.UniqueConstraint(
            "operation_unit_id",
            "effective_from",
            name="uq_staffing_operation_schedules_operation_start",
        ),
        db.Index(
            "ix_staffing_operation_schedules_operation_range",
            "operation_unit_id",
            "effective_from",
            "effective_through",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    operation_unit_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_units.id"),
        nullable=False,
        index=True,
    )
    effective_from = db.Column(db.Date, nullable=False, index=True)
    effective_through = db.Column(db.Date, nullable=True, index=True)
    sunday = db.Column(db.Boolean, nullable=False, default=False)
    monday = db.Column(db.Boolean, nullable=False, default=False)
    tuesday = db.Column(db.Boolean, nullable=False, default=False)
    wednesday = db.Column(db.Boolean, nullable=False, default=False)
    thursday = db.Column(db.Boolean, nullable=False, default=False)
    friday = db.Column(db.Boolean, nullable=False, default=False)
    saturday = db.Column(db.Boolean, nullable=False, default=False)
    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )
    updated_by_user_id = db.Column(
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

    operation = db.relationship(
        "StaffingUnit",
        back_populates="operation_schedules",
    )
    created_by_user = db.relationship("User", foreign_keys=[created_by_user_id])
    updated_by_user = db.relationship("User", foreign_keys=[updated_by_user_id])

    @property
    def active_weekdays(self):
        return frozenset(
            weekday
            for weekday in STAFFING_OPERATION_SCHEDULE_WEEKDAYS
            if bool(getattr(self, weekday))
        )
