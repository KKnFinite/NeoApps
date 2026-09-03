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
            "status IN ('available', 'unavailable_oos', 'topping_off', "
            "'needs_sump')",
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
    apu_running = db.Column(db.Boolean, nullable=True)
    apu_confirmed_at_utc = db.Column(db.DateTime, nullable=True)
    apu_allowance_lbs = db.Column(db.Integer, nullable=True)
    automatic_apu_allowance_lbs = db.Column(db.Integer, nullable=True)
    apu_override_enabled = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=db.false(),
    )
    apu_override_allowance_lbs = db.Column(db.Integer, nullable=True)
    applied_apu_rate_thousand_lbs_per_hour = db.Column(
        db.Numeric(8, 4),
        nullable=True,
    )
    apu_source_tank_code = db.Column(db.String(32), nullable=True)
    off_at_utc = db.Column(db.DateTime, nullable=True)
    off_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )
    truck_segment_started_at_utc = db.Column(db.DateTime, nullable=True)
    ended_early_at_utc = db.Column(db.DateTime, nullable=True)
    ended_early_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )
    ended_early_reason = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    fuel_assignment = db.relationship("NeoScorpionFuelAssignment")
    off_by_user = db.relationship("User", foreign_keys=[off_by_user_id])
    ended_early_by_user = db.relationship(
        "User",
        foreign_keys=[ended_early_by_user_id],
    )
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
        db.CheckConstraint(
            "operational_status IN ('active', 'hold_review')",
            name="ck_neoscorpion_fuel_assignment_operational_status",
        ),
        db.CheckConstraint(
            "current_cycle_type IN ('fuel', 'uplift', 'defuel')",
            name="ck_neoscorpion_fuel_assignment_cycle_type",
        ),
        db.CheckConstraint(
            "current_cycle_number >= 1",
            name="ck_neoscorpion_fuel_assignment_cycle_number_positive",
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
    fuel_on_board_at_utc = db.Column(db.DateTime, nullable=True)
    fuel_on_board_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )
    # A compact operational milestone.  It is intentionally separate from
    # Fuel On Board so current-sort SPEAR timing can distinguish arrival,
    # aircraft readiness, and physical fuel work without creating telemetry.
    ready_for_fuel_at_utc = db.Column(db.DateTime, nullable=True)
    ready_for_fuel_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )
    completed_at_utc = db.Column(db.DateTime, nullable=True)
    completed_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )
    confirmed_tail_number = db.Column(db.String(32), nullable=True)
    operational_status = db.Column(
        db.String(32),
        nullable=False,
        default="active",
        server_default="active",
    )
    hold_reason = db.Column(db.Text, nullable=True)
    hold_at_utc = db.Column(db.DateTime, nullable=True)
    hold_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )
    current_cycle_type = db.Column(
        db.String(16),
        nullable=False,
        default="fuel",
        server_default="fuel",
    )
    current_cycle_number = db.Column(
        db.Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    fueler_update_version = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    fueler_update_message = db.Column(db.Text, nullable=True)
    fueler_update_at_utc = db.Column(db.DateTime, nullable=True)
    fueler_update_acknowledged_version = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    sort_date_operation = db.relationship("SortDateOperation")
    sort_date_mission = db.relationship("SortDateMission")
    assigned_fueler = db.relationship(
        "User",
        foreign_keys=[assigned_fueler_user_id],
    )
    fuel_on_board_by_user = db.relationship(
        "User",
        foreign_keys=[fuel_on_board_by_user_id],
    )
    ready_for_fuel_by_user = db.relationship(
        "User",
        foreign_keys=[ready_for_fuel_by_user_id],
    )
    completed_by_user = db.relationship(
        "User",
        foreign_keys=[completed_by_user_id],
    )
    hold_by_user = db.relationship(
        "User",
        foreign_keys=[hold_by_user_id],
    )
    assigned_truck = db.relationship("NeoScorpionFuelTruck")

    @validates("confirmed_tail_number")
    def _normalize_confirmed_tail_number(self, _key, value):
        normalized = (value or "").strip().upper()
        return normalized or None


class NeoScorpionFuelingEvent(db.Model):
    __tablename__ = "neoscorpion_fueling_events"
    __table_args__ = (
        db.UniqueConstraint(
            "fuel_work_state_id",
            "sequence_number",
            name="uq_neoscorpion_fueling_event_work_sequence",
        ),
        db.CheckConstraint(
            "sequence_number >= 1",
            name="ck_neoscorpion_fueling_event_sequence_positive",
        ),
        db.CheckConstraint(
            "transfer_fuel_gallons IS NULL OR transfer_fuel_gallons >= 0",
            name="ck_neoscorpion_fueling_event_transfer_nonnegative",
        ),
        db.CheckConstraint(
            "event_type IN ('fuel', 'uplift', 'defuel')",
            name="ck_neoscorpion_fueling_event_type",
        ),
        db.CheckConstraint(
            "cycle_number >= 1",
            name="ck_neoscorpion_fueling_event_cycle_number_positive",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_operations.id"),
        nullable=False,
        index=True,
    )
    fuel_assignment_id = db.Column(
        db.Integer,
        db.ForeignKey("neoscorpion_fuel_assignments.id"),
        nullable=False,
        index=True,
    )
    fuel_work_state_id = db.Column(
        db.Integer,
        db.ForeignKey("neoscorpion_fuel_work_states.id"),
        nullable=False,
    )
    tail_number = db.Column(db.String(32), nullable=False)
    fuel_truck_id = db.Column(
        db.Integer,
        db.ForeignKey("neoscorpion_fuel_trucks.id"),
        nullable=False,
    )
    sequence_number = db.Column(db.Integer, nullable=False)
    event_type = db.Column(
        db.String(16),
        nullable=False,
        default="fuel",
        server_default="fuel",
    )
    cycle_number = db.Column(
        db.Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    started_at_utc = db.Column(db.DateTime, nullable=True)
    ended_at_utc = db.Column(db.DateTime, nullable=True)
    transfer_fuel_gallons = db.Column(db.Integer, nullable=True)
    fueler_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )
    required_fuel_lbs = db.Column(db.Integer, nullable=True)
    apu_running = db.Column(db.Boolean, nullable=True)
    apu_allowance_lbs = db.Column(db.Integer, nullable=True)
    apu_source_tank_code = db.Column(db.String(32), nullable=True)
    neo_fuel_lbs = db.Column(db.Integer, nullable=True)
    center_fuel_lbs = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    sort_date_operation = db.relationship("SortDateOperation")
    fuel_assignment = db.relationship("NeoScorpionFuelAssignment")
    fuel_work_state = db.relationship("NeoScorpionFuelWorkState")
    fuel_truck = db.relationship("NeoScorpionFuelTruck")
    fueler_user = db.relationship("User", foreign_keys=[fueler_user_id])
    tank_snapshots = db.relationship(
        "NeoScorpionFuelingEventTankSnapshot",
        back_populates="fueling_event",
        cascade="all, delete-orphan",
    )

    @validates("tail_number")
    def _normalize_tail_number(self, _key, value):
        normalized = (value or "").strip().upper()
        if not normalized:
            raise ValueError("Fueling event requires a tail number.")
        return normalized


class NeoScorpionFuelingEventTankSnapshot(db.Model):
    __tablename__ = "neoscorpion_fueling_event_tank_snapshots"
    __table_args__ = (
        db.UniqueConstraint(
            "fueling_event_id",
            "tank_code",
            name="uq_neoscorpion_fueling_event_tank_snapshot_event_tank",
        ),
        db.CheckConstraint(
            "remaining_lbs IS NULL OR remaining_lbs >= 0",
            name="ck_neoscorpion_event_tank_snapshot_remaining_nonnegative",
        ),
        db.CheckConstraint(
            "actual_lbs IS NULL OR actual_lbs >= 0",
            name="ck_neoscorpion_event_tank_snapshot_actual_nonnegative",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    fueling_event_id = db.Column(
        db.Integer,
        db.ForeignKey("neoscorpion_fueling_events.id"),
        nullable=False,
    )
    tank_code = db.Column(db.String(32), nullable=False)
    remaining_lbs = db.Column(db.Integer, nullable=True)
    planned_lbs = db.Column(db.Integer, nullable=True)
    actual_lbs = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    fueling_event = db.relationship(
        "NeoScorpionFuelingEvent",
        back_populates="tank_snapshots",
    )


class NeoScorpionFuelAuditEntry(db.Model):
    __tablename__ = "neoscorpion_fuel_audit_entries"
    __table_args__ = (
        db.CheckConstraint(
            "action IN ('reopen_off', 'correct_actual', 'auto_hold', "
            "'resume_hold', 'swap_fueler', 'swap_truck', 'confirm_tail', "
            "'end_early')",
            name="ck_neoscorpion_fuel_audit_entry_action",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_operations.id"),
        nullable=False,
        index=True,
    )
    fuel_assignment_id = db.Column(
        db.Integer,
        db.ForeignKey("neoscorpion_fuel_assignments.id"),
        nullable=False,
        index=True,
    )
    fuel_work_state_id = db.Column(
        db.Integer,
        db.ForeignKey("neoscorpion_fuel_work_states.id"),
        nullable=True,
    )
    action = db.Column(db.String(32), nullable=False)
    field_name = db.Column(db.String(80), nullable=True)
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)
    reason = db.Column(db.Text, nullable=False)
    changed_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    sort_date_operation = db.relationship("SortDateOperation")
    fuel_assignment = db.relationship("NeoScorpionFuelAssignment")
    fuel_work_state = db.relationship("NeoScorpionFuelWorkState")
    changed_by_user = db.relationship("User")


class NeoScorpionSettings(db.Model):
    __tablename__ = "neoscorpion_settings"
    __table_args__ = (
        db.UniqueConstraint("gateway_id", name="uq_neoscorpion_settings_gateway"),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=True, index=True)
    fuel_density_lbs_per_gallon = db.Column(db.Float, nullable=True, default=6.7)
    planning_inbound_fuel_fallback_lbs = db.Column(db.Integer, nullable=True)
    fob_difference_threshold_lbs = db.Column(db.Integer, nullable=True)
    tf_vs_estimated_threshold_lbs = db.Column(db.Integer, nullable=True)
    assignment_setup_minutes = db.Column(db.Numeric(8, 2), nullable=True)
    assignment_finishing_minutes = db.Column(db.Numeric(8, 2), nullable=True)
    assignment_eta_safety_buffer_minutes = db.Column(
        db.Numeric(8, 2),
        nullable=True,
        default=5,
    )
    spear_recommendations_enabled = db.Column(
        db.Boolean, nullable=False, default=True, server_default=db.true()
    )
    spear_automation_enabled = db.Column(
        db.Boolean, nullable=False, default=False, server_default=db.false()
    )
    # Learning capture is deliberately separate from recommendations and
    # automation.  It remains off until a durable external Learning Vault is
    # explicitly configured.
    spear_learning_capture_enabled = db.Column(
        db.Boolean, nullable=False, default=False, server_default=db.false()
    )
    spear_minimum_truck_reserve_gallons = db.Column(
        db.Integer, nullable=False, default=500, server_default="500"
    )
    spear_do_not_top_off_above_percent = db.Column(
        db.Integer, nullable=False, default=70, server_default="70"
    )
    spear_truck_minutes_per_ramp_move = db.Column(
        db.Numeric(8, 2), nullable=False, default=2, server_default="2"
    )
    spear_fueler_begins_at = db.Column(
        db.String(16), nullable=False, default="Remote", server_default="Remote"
    )
    spear_truck_begins_at = db.Column(
        db.String(16), nullable=False, default="Remote", server_default="Remote"
    )
    spear_truck_after_top_off = db.Column(
        db.String(16), nullable=False, default="Remote", server_default="Remote"
    )
    spear_incoming_early_staging_minutes = db.Column(
        db.Integer, nullable=False, default=15, server_default="15"
    )
    spear_recalculation_interval_minutes = db.Column(
        db.Integer, nullable=False, default=2, server_default="2"
    )
    spear_automation_stability_delay_seconds = db.Column(
        db.Integer, nullable=False, default=5, server_default="5"
    )
    spear_priority_order_json = db.Column(db.Text, nullable=True)
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


class NeoScorpionSpearAuditEntry(db.Model):
    """Immutable audit of a dispatcher-approved or automatic SPEAR execution."""

    __tablename__ = "neoscorpion_spear_audit_entries"

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
        nullable=True,
        index=True,
    )
    fuel_assignment_id = db.Column(
        db.Integer,
        db.ForeignKey("neoscorpion_fuel_assignments.id"),
        nullable=True,
    )
    action_type = db.Column(db.String(24), nullable=False)
    execution_mode = db.Column(db.String(24), nullable=False)
    source = db.Column(
        db.String(24),
        nullable=False,
        default="spear_optimizer",
        server_default="spear_optimizer",
    )
    reason = db.Column(db.Text, nullable=False)
    fuel_truck_id = db.Column(
        db.Integer, db.ForeignKey("neoscorpion_fuel_trucks.id"), nullable=True
    )
    fueler_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    projected_start_at_utc = db.Column(db.DateTime, nullable=True)
    projected_complete_at_utc = db.Column(db.DateTime, nullable=True)
    risk_classification = db.Column(db.String(24), nullable=True)
    superseded_entry_id = db.Column(
        db.Integer,
        db.ForeignKey("neoscorpion_spear_audit_entries.id"),
        nullable=True,
    )
    recommendation_json = db.Column(db.Text, nullable=False, default="{}")
    executed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    sort_date_operation = db.relationship("SortDateOperation")
    sort_date_mission = db.relationship("SortDateMission")
    fuel_assignment = db.relationship("NeoScorpionFuelAssignment")
    fuel_truck = db.relationship("NeoScorpionFuelTruck")
    fueler_user = db.relationship("User", foreign_keys=[fueler_user_id])
    executed_by_user = db.relationship("User", foreign_keys=[executed_by_user_id])
    superseded_entry = db.relationship(
        "NeoScorpionSpearAuditEntry", remote_side=[id], uselist=False
    )


class NeoScorpionSpearCalibrationReset(db.Model):
    """Current-sort cutoff marker; source operational facts are never deleted."""

    __tablename__ = "neoscorpion_spear_calibration_resets"
    __table_args__ = (
        db.UniqueConstraint(
            "sort_date_operation_id", "metric", "scope_key",
            name="uq_neoscorpion_spear_calibration_reset_scope",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(
        db.Integer, db.ForeignKey("sort_date_operations.id"), nullable=False, index=True
    )
    metric = db.Column(db.String(32), nullable=False)
    scope_key = db.Column(db.String(96), nullable=False)
    observed_after_utc = db.Column(db.DateTime, nullable=False)
    reset_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    sort_date_operation = db.relationship("SortDateOperation")
    reset_by_user = db.relationship("User")


class NeoScorpionAircraftFuelSetting(db.Model):
    __tablename__ = "neoscorpion_aircraft_fuel_settings"
    __table_args__ = (
        db.UniqueConstraint(
            "gateway_id",
            "aircraft_type",
            name="uq_neoscorpion_aircraft_fuel_setting_gateway_type",
        ),
        db.CheckConstraint(
            "aircraft_type IN ('A300', 'B757', 'B767ER', 'B747-400', 'B747-8')",
            name="ck_neoscorpion_aircraft_fuel_setting_type",
        ),
        db.CheckConstraint(
            "apu_rate_thousand_lbs_per_hour >= 0",
            name="ck_neoscorpion_aircraft_fuel_setting_apu_rate_nonnegative",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(
        db.Integer,
        db.ForeignKey("gateways.id"),
        nullable=False,
    )
    aircraft_type = db.Column(db.String(24), nullable=False)
    apu_rate_thousand_lbs_per_hour = db.Column(
        db.Numeric(8, 4),
        nullable=False,
    )
    assignment_pump_rate_gallons_per_minute = db.Column(
        db.Numeric(10, 2),
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

    gateway = db.relationship("Gateway")
    updated_by = db.relationship("User")
