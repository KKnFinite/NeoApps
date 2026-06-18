from datetime import datetime

from app.extensions import db


class SortDateMission(db.Model):
    __tablename__ = "sort_date_missions"
    __table_args__ = (
        db.CheckConstraint(
            "mission_type IN ('arrival', 'departure')",
            name="ck_sort_date_missions_mission_type",
        ),
        db.CheckConstraint(
            "mission_source IN ('master', 'api', 'manual')",
            name="ck_sort_date_missions_mission_source",
        ),
        db.CheckConstraint(
            "wave IS NULL OR wave IN ('1', '2', '1st Wave', '2nd Wave')",
            name="ck_sort_date_missions_wave",
        ),
        db.CheckConstraint(
            "pull_time_source IS NULL OR pull_time_source IN ('master', 'manual')",
            name="ck_sort_date_missions_pull_time_source",
        ),
        db.CheckConstraint(
            "fuel_status IN ('waiting', 'received', 'assigned', 'complete')",
            name="ck_sort_date_missions_fuel_status",
        ),
        db.CheckConstraint(
            "arrival_status IS NULL OR arrival_status IN "
            "('scheduled', 'en_route', 'arrived', 'unloaded')",
            name="ck_sort_date_missions_arrival_status",
        ),
        db.CheckConstraint(
            "departure_status IN ('loading', 'last_uld_enroute', 'ramp_load_complete', "
            "'crew_load_complete', 'blocked_out')",
            name="ck_sort_date_missions_departure_status",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_date = db.Column(db.Date, nullable=False, index=True)
    gateway_code = db.Column(db.String(8), nullable=False, index=True)
    sort_name = db.Column(db.String(32), nullable=False, index=True)
    sort_date_operation_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_operations.id"),
        nullable=False,
        index=True,
    )
    mission_type = db.Column(db.String(16), nullable=False, index=True)
    mission_source = db.Column(db.String(32), nullable=False, default="master")
    wave = db.Column(db.String(16), nullable=True, index=True)
    master_flight_schedule_id = db.Column(
        db.Integer,
        db.ForeignKey("master_flight_schedules.id"),
        nullable=True,
        index=True,
    )
    flight_number = db.Column(db.String(32), nullable=False)
    origin = db.Column(db.String(8), nullable=False)
    destination = db.Column(db.String(8), nullable=False)
    timezone = db.Column(db.String(64), nullable=False, default="America/Chicago")
    planned_datetime_local = db.Column(db.DateTime, nullable=False)
    planned_datetime_utc = db.Column(db.DateTime, nullable=False, index=True)
    planned_source = db.Column(db.String(32), nullable=False, default="unknown")
    eta_datetime_utc = db.Column(db.DateTime, nullable=True)
    eta_source = db.Column(db.String(32), nullable=False, default="unknown")
    api_status = db.Column(db.String(32), nullable=True)
    api_runway_time_utc = db.Column(db.DateTime, nullable=True)
    api_assumed_arrived_time_utc = db.Column(db.DateTime, nullable=True)
    api_aircraft_model = db.Column(db.String(120), nullable=True)
    api_last_seen_at_utc = db.Column(db.DateTime, nullable=True)
    api_added_current_sort_only = db.Column(db.Boolean, nullable=False, default=False)
    actual_block_in_datetime_utc = db.Column(db.DateTime, nullable=True)
    actual_block_in_source = db.Column(db.String(32), nullable=False, default="unknown")
    actual_block_out_datetime_utc = db.Column(db.DateTime, nullable=True)
    actual_block_out_source = db.Column(db.String(32), nullable=False, default="unknown")
    assigned_tail_number = db.Column(db.String(32), nullable=True)
    tail_source = db.Column(db.String(32), nullable=False, default="unknown")
    tail_updated_at = db.Column(db.DateTime, nullable=True)
    planned_fuel_load = db.Column(db.Integer, nullable=True)
    planned_fuel_updated_at = db.Column(db.DateTime, nullable=True)
    pure_pull_time_local = db.Column(db.Time, nullable=True)
    first_mix_pull_time_local = db.Column(db.Time, nullable=True)
    final_mix_pull_time_local = db.Column(db.Time, nullable=True)
    actual_pure_pull_time_local = db.Column(db.Time, nullable=True)
    actual_first_mix_pull_time_local = db.Column(db.Time, nullable=True)
    actual_second_mix_pull_time_local = db.Column(db.Time, nullable=True)
    pull_time_source = db.Column(db.String(32), nullable=True)
    fuel_status = db.Column(db.String(32), nullable=True)
    fuel_completed_at_utc = db.Column(db.DateTime, nullable=True)
    arrival_status = db.Column(db.String(32), nullable=True)
    departure_status = db.Column(db.String(32), nullable=True)
    last_uld_enroute_at_utc = db.Column(db.DateTime, nullable=True)
    ramp_load_completed_at_utc = db.Column(db.DateTime, nullable=True)
    crew_load_completed_at_utc = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    sort_date_operation = db.relationship("SortDateOperation", back_populates="missions")
    crew_assignments = db.relationship(
        "SortDateCrewAssignment",
        back_populates="sort_date_mission",
    )
