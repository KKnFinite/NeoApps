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
