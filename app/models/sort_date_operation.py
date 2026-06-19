from datetime import datetime

from sqlalchemy.orm import validates

from app.extensions import db


class SortDateOperation(db.Model):
    __tablename__ = "sort_date_operations"
    __table_args__ = (
        db.CheckConstraint(
            "window_minutes >= 0",
            name="ck_sort_date_operations_window_minutes_nonnegative",
        ),
        db.CheckConstraint(
            "first_wave_window_minutes IS NULL OR first_wave_window_minutes >= 0",
            name="ck_sort_date_operations_first_wave_window_minutes_nonnegative",
        ),
        db.CheckConstraint(
            "second_wave_window_minutes IS NULL OR second_wave_window_minutes >= 0",
            name="ck_sort_date_operations_second_wave_window_minutes_nonnegative",
        ),
        db.UniqueConstraint(
            "sort_date",
            "gateway_code",
            "sort_name",
            name="uq_sort_date_operations_gateway_date_sort",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=True, index=True)
    sort_date = db.Column(db.Date, nullable=False, index=True)
    gateway_code = db.Column(db.String(8), nullable=False, index=True)
    sort_name = db.Column(db.String(32), nullable=False, index=True)
    window_minutes = db.Column(db.Integer, nullable=False, default=0)
    first_wave_window_minutes = db.Column(db.Integer, nullable=True)
    second_wave_window_minutes = db.Column(db.Integer, nullable=True)
    generated_at_utc = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    generated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    locked_at_utc = db.Column(db.DateTime, nullable=True)
    archived_at_utc = db.Column(db.DateTime, nullable=True)
    flight_api_last_attempted_poll_at_utc = db.Column(db.DateTime, nullable=True)
    flight_api_last_successful_poll_at_utc = db.Column(db.DateTime, nullable=True)
    flight_api_last_failed_poll_at_utc = db.Column(db.DateTime, nullable=True)
    flight_api_last_poll_status = db.Column(db.String(32), nullable=False, default="")
    flight_api_last_poll_summary = db.Column(db.String(255), nullable=False, default="")
    flight_api_next_auto_poll_eligible_at_utc = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    missions = db.relationship("SortDateMission", back_populates="sort_date_operation")
    gateway = db.relationship("Gateway")
    generated_by_user = db.relationship("User")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.window_minutes is None:
            self.window_minutes = 0

    @validates("window_minutes")
    def validate_window_minutes(self, key, value):
        if value is None:
            return 0

        value = int(value)
        if value < 0:
            raise ValueError("window_minutes cannot be negative.")

        return value

    @validates("first_wave_window_minutes", "second_wave_window_minutes")
    def validate_optional_window_minutes(self, key, value):
        if value in (None, ""):
            return None

        value = int(value)
        if value < 0:
            raise ValueError(f"{key} cannot be negative.")

        return value
