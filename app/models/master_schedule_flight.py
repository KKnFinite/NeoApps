from datetime import datetime

from app.extensions import db


class MasterScheduleFlight(db.Model):
    __tablename__ = "master_schedule_flights"
    __table_args__ = (
        db.CheckConstraint(
            "schedule_type IN ('inbound', 'outbound')",
            name="ck_master_schedule_flights_schedule_type",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_code = db.Column(db.String(8), nullable=False, index=True)
    sort_name = db.Column(db.String(32), nullable=False, index=True)
    schedule_type = db.Column(db.String(16), nullable=False, index=True)
    flight_number = db.Column(db.String(32), nullable=False)
    origin = db.Column(db.String(8), nullable=False)
    destination = db.Column(db.String(8), nullable=False)
    active_days = db.Column(db.String(128), nullable=False)
    planned_time_local = db.Column(db.Time, nullable=False)
    timezone = db.Column(db.String(64), nullable=False, default="America/Chicago")
    preferred_parking = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
