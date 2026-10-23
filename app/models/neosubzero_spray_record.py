from datetime import datetime

from app.extensions import db


class NeoSubZeroSprayRecord(db.Model):
    """One tail-owned pass participant; measured gallons are independent evidence."""

    __tablename__ = "neosubzero_spray_records"
    __table_args__ = (
        db.CheckConstraint('(departure_deice_event_id IS NOT NULL AND pretreat_state_id IS NULL) OR (departure_deice_event_id IS NULL AND pretreat_state_id IS NOT NULL)',name='ck_neosubzero_spray_one_parent'),
        db.CheckConstraint("pass_number IN (1, 2)", name="ck_neosubzero_spray_pass"),
        db.CheckConstraint(
            "position_number BETWEEN 1 AND 4",
            name="ck_neosubzero_spray_position",
        ),
        db.CheckConstraint("gallons > 0", name="ck_neosubzero_spray_gallons_positive"),
        db.CheckConstraint(
            "pass_type IN ('type_i', 'type_iv')",
            name="ck_neosubzero_spray_type",
        ),
        db.CheckConstraint(
            "surface_area IN ('wings_only', 'wings_tail', 'entire_aircraft')",
            name="ck_neosubzero_spray_surface",
        ),
        db.UniqueConstraint(
            "departure_deice_event_id",
            "pass_number",
            "position_number",
            name="uq_neosubzero_spray_event_pass_position",
        ),
        db.UniqueConstraint('pretreat_state_id','pass_number','position_number',name='uq_neosubzero_spray_pretreat_pass_position'),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_operations.id"),
        nullable=False,
        index=True,
    )
    sort_date_mission_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_missions.id"),
        nullable=False,
        index=True,
    )
    departure_deice_event_id = db.Column(
        db.Integer,
        db.ForeignKey("neosubzero_departure_deice_events.id"),
        nullable=True,
        index=True,
    )
    pretreat_state_id = db.Column(db.Integer, db.ForeignKey('neosubzero_pretreat_states.id'), nullable=True, index=True)
    tail_number_snapshot = db.Column(db.String(32), nullable=True)
    flight_number_snapshot = db.Column(db.String(32), nullable=True)
    timezone_snapshot = db.Column(db.String(64), nullable=True)
    ramp_snapshot = db.Column(db.String(16), nullable=True)
    parking_snapshot = db.Column(db.String(32), nullable=True)
    official_at_utc = db.Column(db.DateTime, nullable=True)
    participation_selected = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    crew_override = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    excluded_at_utc = db.Column(db.DateTime, nullable=True)
    pass_number = db.Column(db.Integer, nullable=False)
    position_number = db.Column(db.Integer, nullable=False)
    gallons = db.Column(db.Numeric(8, 2), nullable=True)
    truck_number_snapshot = db.Column(db.String(32), nullable=False)
    pass_type = db.Column(db.String(16), nullable=False)
    fluid_name_snapshot = db.Column(db.String(80), nullable=False)
    concentration_percent_snapshot = db.Column(db.Integer, nullable=False)
    surface_area = db.Column(db.String(32), nullable=True)
    started_at_utc = db.Column(db.DateTime, nullable=True)
    ended_at_utc = db.Column(db.DateTime, nullable=True)
    driver_person_id = db.Column(db.Integer, db.ForeignKey("staffing_people.id"), nullable=True)
    driver_name_snapshot = db.Column(db.String(160), nullable=True)
    flyer_person_id = db.Column(db.Integer, db.ForeignKey("staffing_people.id"), nullable=True)
    flyer_name_snapshot = db.Column(db.String(160), nullable=True)
    reason_for_application = db.Column(db.String(120), nullable=True)
    active_precipitation = db.Column(db.String(120), nullable=True)
    ambient_temperature = db.Column(db.String(32), nullable=True)
    dew_point = db.Column(db.String(32), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    recorded_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    operation = db.relationship("SortDateOperation")
    mission = db.relationship("SortDateMission")
    departure_deice_event = db.relationship("NeoSubZeroDepartureDeiceEvent")
    pretreat_state = db.relationship('NeoSubZeroPretreatState')
    driver_person = db.relationship("StaffingPerson", foreign_keys=[driver_person_id])
    flyer_person = db.relationship("StaffingPerson", foreign_keys=[flyer_person_id])
    recorded_by_user = db.relationship("User")
