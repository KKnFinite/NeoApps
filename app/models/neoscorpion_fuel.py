from datetime import datetime

from app.extensions import db


class NeoScorpionTailFuelState(db.Model):
    __tablename__ = "neoscorpion_tail_fuel_states"
    __table_args__ = (
        db.UniqueConstraint(
            "sort_date_operation_id",
            "tail_number",
            name="uq_neoscorpion_tail_fuel_state_operation_tail",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_operations.id"),
        nullable=False,
        index=True,
    )
    sort_date_tail_state_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_tail_states.id"),
        nullable=True,
        index=True,
    )
    tail_number = db.Column(db.String(32), nullable=False, index=True)
    inbound_fuel_lbs = db.Column(db.Integer, nullable=True)
    fob_lbs = db.Column(db.Integer, nullable=True)
    actual_fuel_lbs = db.Column(db.Integer, nullable=True)
    center_fuel_lbs = db.Column(db.Integer, nullable=True)
    apu_lbs = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.Text, nullable=False, default="")
    status = db.Column(db.String(32), nullable=False, default="pending")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    sort_date_operation = db.relationship("SortDateOperation")
    sort_date_tail_state = db.relationship("SortDateTailState")


class NeoScorpionFuelTruck(db.Model):
    __tablename__ = "neoscorpion_fuel_trucks"
    __table_args__ = (
        db.UniqueConstraint(
            "gateway_id",
            "truck_number",
            name="uq_neoscorpion_fuel_truck_gateway_number",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=True, index=True)
    truck_number = db.Column(db.String(40), nullable=False, index=True)
    description = db.Column(db.String(160), nullable=False, default="")
    capacity_gallons = db.Column(db.Integer, nullable=True)
    remaining_fuel_gallons = db.Column(db.Integer, nullable=True)
    vendor_driver_name = db.Column(db.String(120), nullable=False, default="")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_out_of_service = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gateway = db.relationship("Gateway")


class NeoScorpionFuelAssignment(db.Model):
    __tablename__ = "neoscorpion_fuel_assignments"
    __table_args__ = (
        db.UniqueConstraint(
            "sort_date_mission_id",
            name="uq_neoscorpion_fuel_assignment_mission",
        ),
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
    assigned_fueler_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    assigned_truck_id = db.Column(
        db.Integer,
        db.ForeignKey("neoscorpion_fuel_trucks.id"),
        nullable=True,
    )
    transfer_fuel_gallons = db.Column(db.Integer, nullable=True)
    estimated_fuel_gallons = db.Column(db.Integer, nullable=True)
    calculation_status = db.Column(db.String(32), nullable=False, default="not_configured")
    review_status = db.Column(db.String(32), nullable=False, default="pending")
    load_planning_note = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    sort_date_operation = db.relationship("SortDateOperation")
    sort_date_mission = db.relationship("SortDateMission")
    assigned_fueler = db.relationship("User")
    assigned_truck = db.relationship("NeoScorpionFuelTruck")


class NeoScorpionSettings(db.Model):
    __tablename__ = "neoscorpion_settings"
    __table_args__ = (
        db.UniqueConstraint("gateway_id", name="uq_neoscorpion_settings_gateway"),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=True, index=True)
    fuel_density_lbs_per_gallon = db.Column(db.Float, nullable=True, default=6.7)
    fob_difference_threshold_lbs = db.Column(db.Integer, nullable=True)
    tf_vs_estimated_threshold_lbs = db.Column(db.Integer, nullable=True)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gateway = db.relationship("Gateway")
    updated_by = db.relationship("User")
