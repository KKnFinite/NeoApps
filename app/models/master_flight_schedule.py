from datetime import datetime

from app.extensions import db


class MasterFlightSchedule(db.Model):
    __tablename__ = "master_flight_schedules"
    __table_args__ = (
        db.CheckConstraint(
            "mission_type IN ('arrival', 'departure')",
            name="ck_master_flight_schedules_mission_type",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_code = db.Column(db.String(8), nullable=False, index=True)
    sort_name = db.Column(db.String(32), nullable=False, index=True)
    mission_type = db.Column(db.String(16), nullable=False, index=True)
    flight_number = db.Column(db.String(32), nullable=False)
    origin = db.Column(db.String(8), nullable=False)
    destination = db.Column(db.String(8), nullable=False)
    active_days = db.Column(db.String(128), nullable=False)
    planned_time_local = db.Column(db.Time, nullable=False)
    timezone = db.Column(db.String(64), nullable=False, default="America/Chicago")
    preferred_parking = db.Column(db.String(64), nullable=True)
    pure_pull_time_local = db.Column(db.Time, nullable=True)
    first_mix_pull_time_local = db.Column(db.Time, nullable=True)
    final_mix_pull_time_local = db.Column(db.Time, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
