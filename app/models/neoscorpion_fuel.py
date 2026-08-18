from datetime import datetime

from sqlalchemy.orm import validates

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


class NeoScorpionSortAssetState(db.Model):
    __tablename__ = "neoscorpion_sort_asset_states"
    __table_args__ = (
        db.UniqueConstraint(
            "sort_date_operation_id",
            name="uq_neoscorpion_sort_asset_state_operation",
        ),
        db.CheckConstraint(
            "fuel_island_count IS NULL OR fuel_island_count BETWEEN 0 AND 4",
            name="ck_neoscorpion_sort_asset_state_island_count",
        ),
        db.CheckConstraint(
            "revision >= 0",
            name="ck_neoscorpion_sort_asset_state_revision_nonnegative",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_operations.id"),
        nullable=False,
    )
    fuel_island_count = db.Column(db.Integer, nullable=True)
    revision = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    sort_date_operation = db.relationship("SortDateOperation")


class NeoScorpionSortFueler(db.Model):
    __tablename__ = "neoscorpion_sort_fuelers"
    __table_args__ = (
        db.UniqueConstraint(
            "sort_date_operation_id",
            "user_id",
            name="uq_neoscorpion_sort_fueler_operation_user",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_operations.id"),
        nullable=False,
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    sort_date_operation = db.relationship("SortDateOperation")
    user = db.relationship("User")


class NeoScorpionSortTruck(db.Model):
    __tablename__ = "neoscorpion_sort_trucks"
    __table_args__ = (
        db.UniqueConstraint(
            "sort_date_operation_id",
            "fuel_truck_id",
            name="uq_neoscorpion_sort_truck_operation_truck",
        ),
        db.CheckConstraint(
            "status IN ('available', 'unavailable_oos', 'topping_off')",
            name="ck_neoscorpion_sort_truck_status",
        ),
        db.CheckConstraint(
            "starting_gallons IS NULL OR starting_gallons >= 0",
            name="ck_neoscorpion_sort_truck_starting_gallons_nonnegative",
        ),
        db.CheckConstraint(
            "current_gallons IS NULL OR current_gallons >= 0",
            name="ck_neoscorpion_sort_truck_current_gallons_nonnegative",
        ),
        db.CheckConstraint(
            "status <> 'available' OR "
            "(starting_gallons IS NOT NULL AND current_gallons IS NOT NULL)",
            name="ck_neoscorpion_sort_truck_available_gallons",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_operations.id"),
        nullable=False,
    )
    fuel_truck_id = db.Column(
        db.Integer,
        db.ForeignKey("neoscorpion_fuel_trucks.id"),
        nullable=False,
    )
    status = db.Column(db.String(32), nullable=False)
    starting_gallons = db.Column(db.Integer, nullable=True)
    current_gallons = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    sort_date_operation = db.relationship("SortDateOperation")
    fuel_truck = db.relationship("NeoScorpionFuelTruck")


class NeoScorpionFuelWorkState(db.Model):
    __tablename__ = "neoscorpion_fuel_work_states"
    __table_args__ = (
        db.UniqueConstraint(
            "fuel_assignment_id",
            "tail_number",
            name="uq_neoscorpion_fuel_work_state_assignment_tail",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    fuel_assignment_id = db.Column(
        db.Integer,
        db.ForeignKey("neoscorpion_fuel_assignments.id"),
        nullable=False,
    )
    tail_number = db.Column(db.String(32), nullable=False)
    on_at_utc = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    fuel_assignment = db.relationship("NeoScorpionFuelAssignment")
    tank_states = db.relationship(
        "NeoScorpionFuelTankState",
        back_populates="fuel_work_state",
        cascade="all, delete-orphan",
    )

    @validates("tail_number")
    def _normalize_tail_number(self, _key, value):
        normalized = (value or "").strip().upper()
        if not normalized:
            raise ValueError("Fuel work state requires a tail number.")
        return normalized


class NeoScorpionFuelTankState(db.Model):
    __tablename__ = "neoscorpion_fuel_tank_states"
    __table_args__ = (
        db.UniqueConstraint(
            "fuel_work_state_id",
            "tank_code",
            name="uq_neoscorpion_fuel_tank_state_work_tank",
        ),
        db.CheckConstraint(
            "remaining_lbs IS NULL OR remaining_lbs >= 0",
            name="ck_neoscorpion_fuel_tank_state_remaining_nonnegative",
        ),
        db.CheckConstraint(
            "actual_lbs IS NULL OR actual_lbs >= 0",
            name="ck_neoscorpion_fuel_tank_state_actual_nonnegative",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    fuel_work_state_id = db.Column(
        db.Integer,
        db.ForeignKey("neoscorpion_fuel_work_states.id"),
        nullable=False,
    )
    tank_code = db.Column(db.String(32), nullable=False)
    remaining_lbs = db.Column(db.Integer, nullable=True)
    actual_lbs = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    fuel_work_state = db.relationship(
        "NeoScorpionFuelWorkState",
        back_populates="tank_states",
    )


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
