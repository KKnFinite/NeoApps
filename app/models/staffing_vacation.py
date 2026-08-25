from datetime import datetime

from app.extensions import db


class StaffingVacationUnionCalendar(db.Model):
    """Year-scoped Union vacation capacity pool definition."""

    __tablename__ = "staffing_vacation_union_calendars"
    __table_args__ = (
        db.CheckConstraint(
            "vacation_year >= 2000 AND vacation_year <= 2200",
            name="ck_staffing_vacation_union_calendars_year",
        ),
        db.CheckConstraint(
            "include_part_time OR include_full_time",
            name="ck_staffing_vacation_union_calendars_classification",
        ),
        db.UniqueConstraint(
            "vacation_year",
            "operation_unit_id",
            "name",
            name="uq_staffing_vacation_union_calendars_year_operation_name",
        ),
        db.Index(
            "ix_staffing_vacation_union_calendars_year_operation",
            "vacation_year",
            "operation_unit_id",
            "active",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    vacation_year = db.Column(db.Integer, nullable=False, index=True)
    operation_unit_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_units.id"),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String(140), nullable=False)
    include_part_time = db.Column(db.Boolean, nullable=False, default=True)
    include_full_time = db.Column(db.Boolean, nullable=False, default=False)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    operation = db.relationship("StaffingUnit", foreign_keys=[operation_unit_id])
    scopes = db.relationship(
        "StaffingVacationUnionCalendarScope",
        back_populates="calendar",
        cascade="all, delete-orphan",
        order_by="StaffingVacationUnionCalendarScope.id",
    )
    created_by_user = db.relationship("User", foreign_keys=[created_by_user_id])
    updated_by_user = db.relationship("User", foreign_keys=[updated_by_user_id])


class StaffingVacationUnionCalendarScope(db.Model):
    """Selected hierarchy node for a Union vacation calendar."""

    __tablename__ = "staffing_vacation_union_calendar_scopes"
    __table_args__ = (
        db.UniqueConstraint(
            "calendar_id",
            "staffing_unit_id",
            name="uq_staffing_vacation_union_calendar_scopes_calendar_unit",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    calendar_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_vacation_union_calendars.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    staffing_unit_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_units.id"),
        nullable=False,
        index=True,
    )

    calendar = db.relationship(
        "StaffingVacationUnionCalendar",
        back_populates="scopes",
    )
    staffing_unit = db.relationship("StaffingUnit")


class StaffingVacationUnionSelection(db.Model):
    """Durable whole-week Union selection independent of calendar definitions."""

    __tablename__ = "staffing_vacation_union_selections"
    __table_args__ = (
        db.CheckConstraint(
            "vacation_year >= 2000 AND vacation_year <= 2200",
            name="ck_staffing_vacation_union_selections_year",
        ),
        db.CheckConstraint(
            "bank_type IN ('regular', 'optional')",
            name="ck_staffing_vacation_union_selections_bank_type",
        ),
        db.CheckConstraint(
            "status IN ('pending', 'approved', 'denied', 'cancelled')",
            name="ck_staffing_vacation_union_selections_status",
        ),
        db.UniqueConstraint(
            "staffing_person_id",
            "vacation_year",
            "week_ending",
            name="uq_staffing_vacation_union_selections_person_year_week",
        ),
        db.Index(
            "ix_staffing_vacation_union_selections_year_week_status",
            "vacation_year",
            "week_ending",
            "status",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    staffing_person_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_people.id"),
        nullable=False,
        index=True,
    )
    vacation_year = db.Column(db.Integer, nullable=False, index=True)
    week_ending = db.Column(db.Date, nullable=False, index=True)
    bank_type = db.Column(db.String(16), nullable=False)
    status = db.Column(db.String(16), nullable=False, index=True)
    entered_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    reviewed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    cancelled_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    person = db.relationship("StaffingPerson")
    entered_by_user = db.relationship("User", foreign_keys=[entered_by_user_id])
    reviewed_by_user = db.relationship("User", foreign_keys=[reviewed_by_user_id])
    cancelled_by_user = db.relationship("User", foreign_keys=[cancelled_by_user_id])


class StaffingVacationWeekConversion(db.Model):
    """One durable whole-week entitlement converted into five split days."""

    __tablename__ = "staffing_vacation_week_conversions"
    __table_args__ = (
        db.CheckConstraint(
            "vacation_year >= 2000 AND vacation_year <= 2200",
            name="ck_staffing_vacation_week_conversions_year",
        ),
        db.CheckConstraint(
            "program IN ('management', 'union')",
            name="ck_staffing_vacation_week_conversions_program",
        ),
        db.CheckConstraint(
            "NOT (source_management_selection_id IS NOT NULL "
            "AND source_union_selection_id IS NOT NULL)",
            name="ck_staffing_vacation_week_conversions_one_source",
        ),
        db.Index(
            "ix_staffing_vacation_week_conversions_person_year_program",
            "staffing_person_id",
            "vacation_year",
            "program",
            "recombined_at",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    staffing_person_id = db.Column(
        db.Integer, db.ForeignKey("staffing_people.id"), nullable=False, index=True
    )
    vacation_year = db.Column(db.Integer, nullable=False, index=True)
    program = db.Column(db.String(16), nullable=False, index=True)
    source_management_selection_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_vacation_management_selections.id"),
        nullable=True,
        unique=True,
    )
    source_union_selection_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_vacation_union_selections.id"),
        nullable=True,
        unique=True,
    )
    converted_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    converted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    recombined_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    recombined_at = db.Column(db.DateTime, nullable=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    person = db.relationship("StaffingPerson")
    source_management_selection = db.relationship("StaffingVacationManagementSelection")
    source_union_selection = db.relationship("StaffingVacationUnionSelection")
    days = db.relationship(
        "StaffingVacationDaySelection",
        back_populates="conversion",
        cascade="all, delete-orphan",
        order_by="StaffingVacationDaySelection.vacation_date",
    )


class StaffingVacationDaySelection(db.Model):
    """Sparse reusable person/day time-off boundary across vacation day types."""

    __tablename__ = "staffing_vacation_day_selections"
    __table_args__ = (
        db.CheckConstraint(
            "vacation_year >= 2000 AND vacation_year <= 2200",
            name="ck_staffing_vacation_day_selections_year",
        ),
        db.CheckConstraint(
            "item_type IN ('split_vacation', 'd_day', 'optional_day', "
            "'anniversary_day', 'floating_holiday', 'special_assignment', "
            "'corporate_class')",
            name="ck_staffing_vacation_day_selections_item_type",
        ),
        db.CheckConstraint(
            "status IN ('scheduled', 'cancelled')",
            name="ck_staffing_vacation_day_selections_status",
        ),
        db.Index(
            "ix_staffing_vacation_day_selections_person_date_status",
            "staffing_person_id",
            "vacation_date",
            "status",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    conversion_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_vacation_week_conversions.id"),
        nullable=True,
        index=True,
    )
    entitlement_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_vacation_day_entitlements.id"),
        nullable=True,
        index=True,
    )
    staffing_person_id = db.Column(
        db.Integer, db.ForeignKey("staffing_people.id"), nullable=False, index=True
    )
    vacation_year = db.Column(db.Integer, nullable=False, index=True)
    vacation_date = db.Column(db.Date, nullable=False, index=True)
    item_type = db.Column(db.String(32), nullable=False, default="split_vacation")
    status = db.Column(db.String(16), nullable=False, default="scheduled", index=True)
    entered_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    cancelled_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    conversion = db.relationship("StaffingVacationWeekConversion", back_populates="days")
    entitlement = db.relationship("StaffingVacationDayEntitlement")
    person = db.relationship("StaffingPerson")


class StaffingVacationDayEntitlement(db.Model):
    """Durable non-derived day entitlement; currently Floating Holidays only."""

    __tablename__ = "staffing_vacation_day_entitlements"
    __table_args__ = (
        db.CheckConstraint(
            "vacation_year >= 2000 AND vacation_year <= 2200",
            name="ck_staffing_vacation_day_entitlements_year",
        ),
        db.CheckConstraint(
            "entitlement_type IN ('floating_holiday')",
            name="ck_staffing_vacation_day_entitlements_type",
        ),
        db.CheckConstraint(
            "source_program IN ('management', 'union')",
            name="ck_staffing_vacation_day_entitlements_program",
        ),
        db.UniqueConstraint(
            "source_program",
            "source_selection_id",
            "source_holiday_date",
            name="uq_staffing_vacation_day_entitlements_source_holiday",
        ),
        db.Index(
            "ix_staffing_vacation_day_entitlements_person_year_type",
            "staffing_person_id",
            "vacation_year",
            "entitlement_type",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    staffing_person_id = db.Column(
        db.Integer, db.ForeignKey("staffing_people.id"), nullable=False, index=True
    )
    vacation_year = db.Column(db.Integer, nullable=False, index=True)
    entitlement_type = db.Column(db.String(32), nullable=False)
    source_program = db.Column(db.String(16), nullable=False)
    source_selection_id = db.Column(db.Integer, nullable=False)
    source_holiday_date = db.Column(db.Date, nullable=False)
    source_holiday_name = db.Column(db.String(80), nullable=False)
    awarded_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    person = db.relationship("StaffingPerson")


class StaffingVacationManagementCapacity(db.Model):
    """Whole-week Management limits for one hierarchy area and vacation year."""

    __tablename__ = "staffing_vacation_management_capacities"
    __table_args__ = (
        db.CheckConstraint(
            "vacation_year >= 2000 AND vacation_year <= 2200",
            name="ck_staffing_vacation_management_capacities_year",
        ),
        db.CheckConstraint(
            "normal_limit >= 0 AND one_pinned_limit >= 0 AND two_plus_pinned_limit >= 0",
            name="ck_staffing_vacation_management_capacities_limits",
        ),
        db.UniqueConstraint(
            "vacation_year",
            "area_unit_id",
            name="uq_staffing_vacation_management_capacities_year_area",
        ),
        db.Index(
            "ix_staffing_vacation_management_capacities_year_area",
            "vacation_year",
            "area_unit_id",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    vacation_year = db.Column(db.Integer, nullable=False, index=True)
    area_unit_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_units.id"),
        nullable=False,
        index=True,
    )
    normal_limit = db.Column(db.Integer, nullable=False)
    one_pinned_limit = db.Column(db.Integer, nullable=False)
    two_plus_pinned_limit = db.Column(db.Integer, nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    area = db.relationship("StaffingUnit", foreign_keys=[area_unit_id])
    created_by_user = db.relationship("User", foreign_keys=[created_by_user_id])
    updated_by_user = db.relationship("User", foreign_keys=[updated_by_user_id])


class StaffingVacationManagementWeekOverride(db.Model):
    """Sparse explicit OFF exception to default-ON reduced Management capacity."""

    __tablename__ = "staffing_vacation_management_week_overrides"
    __table_args__ = (
        db.CheckConstraint(
            "vacation_year >= 2000 AND vacation_year <= 2200",
            name="ck_staffing_vacation_management_week_overrides_year",
        ),
        db.UniqueConstraint(
            "vacation_year",
            "area_unit_id",
            "week_ending",
            name="uq_staffing_vacation_management_week_overrides_year_area_week",
        ),
        db.Index(
            "ix_staffing_vacation_management_week_overrides_year_area",
            "vacation_year",
            "area_unit_id",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    vacation_year = db.Column(db.Integer, nullable=False, index=True)
    area_unit_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_units.id"),
        nullable=False,
        index=True,
    )
    week_ending = db.Column(db.Date, nullable=False, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    area = db.relationship("StaffingUnit", foreign_keys=[area_unit_id])
    created_by_user = db.relationship("User", foreign_keys=[created_by_user_id])


class StaffingVacationManagementSelection(db.Model):
    """Durable whole-week Management vacation selection for a person/year."""

    __tablename__ = "staffing_vacation_management_selections"
    __table_args__ = (
        db.CheckConstraint(
            "vacation_year >= 2000 AND vacation_year <= 2200",
            name="ck_staffing_vacation_management_selections_year",
        ),
        db.UniqueConstraint(
            "staffing_person_id",
            "vacation_year",
            "week_ending",
            name="uq_staffing_vacation_management_selections_person_year_week",
        ),
        db.Index(
            "ix_staffing_vacation_management_selections_year_week",
            "vacation_year",
            "week_ending",
            "cancelled_at",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    staffing_person_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_people.id"),
        nullable=False,
        index=True,
    )
    vacation_year = db.Column(db.Integer, nullable=False, index=True)
    week_ending = db.Column(db.Date, nullable=False, index=True)
    selected_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    cancelled_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True, index=True)
    cancellation_reason = db.Column(db.String(40), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    person = db.relationship("StaffingPerson")
    selected_by_user = db.relationship("User", foreign_keys=[selected_by_user_id])
    cancelled_by_user = db.relationship("User", foreign_keys=[cancelled_by_user_id])


class StaffingVacationManagementTurnState(db.Model):
    """Minimal persisted cursor for one area's initial seniority turn."""

    __tablename__ = "staffing_vacation_management_turn_states"
    __table_args__ = (
        db.CheckConstraint(
            "vacation_year >= 2000 AND vacation_year <= 2200",
            name="ck_staffing_vacation_management_turn_states_year",
        ),
        db.UniqueConstraint(
            "vacation_year",
            "area_unit_id",
            name="uq_staffing_vacation_management_turn_states_year_area",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    vacation_year = db.Column(db.Integer, nullable=False, index=True)
    area_unit_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_units.id"),
        nullable=False,
        index=True,
    )
    current_person_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_people.id"),
        nullable=True,
        index=True,
    )
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True, index=True)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    area = db.relationship("StaffingUnit", foreign_keys=[area_unit_id])
    current_person = db.relationship("StaffingPerson", foreign_keys=[current_person_id])
    resolutions = db.relationship(
        "StaffingVacationManagementTurnResolution",
        back_populates="turn_state",
        cascade="all, delete-orphan",
        order_by="StaffingVacationManagementTurnResolution.id",
    )


class StaffingVacationManagementTurnResolution(db.Model):
    """Resolved initial-turn participation without persisting roster membership."""

    __tablename__ = "staffing_vacation_management_turn_resolutions"
    __table_args__ = (
        db.CheckConstraint(
            "outcome IN ('completed', 'passed', 'admin_passed', 'transferred', 'departed')",
            name="ck_staffing_vacation_management_turn_resolutions_outcome",
        ),
        db.UniqueConstraint(
            "turn_state_id",
            "staffing_person_id",
            name="uq_staffing_vacation_management_turn_resolutions_state_person",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    turn_state_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_vacation_management_turn_states.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    staffing_person_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_people.id"),
        nullable=False,
        index=True,
    )
    outcome = db.Column(db.String(24), nullable=False)
    resolved_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    resolved_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    turn_state = db.relationship(
        "StaffingVacationManagementTurnState",
        back_populates="resolutions",
    )
    person = db.relationship("StaffingPerson")
    resolved_by_user = db.relationship("User", foreign_keys=[resolved_by_user_id])
