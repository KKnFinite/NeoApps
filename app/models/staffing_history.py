from app.extensions import db
from app.models.staffing_daily_attendance import STAFFING_DAILY_ATTENDANCE_STATUSES


class StaffingRosterRevision(db.Model):
    """Versioned inputs needed to reconstruct the roster at a Sort boundary."""

    __tablename__ = "staffing_roster_revisions"
    __table_args__ = (
        db.CheckConstraint("kind IN ('baseline', 'unit', 'person', 'assignment')", name="ck_staffing_roster_revision_kind"),
        db.Index("ix_staffing_roster_revision_lookup", "kind", "entity_id", "changed_at", "id"),
        db.Index("ix_staffing_roster_revision_cutoff", "changed_at", "change_set_id"),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer(), "sqlite"), primary_key=True)
    kind = db.Column(db.String(12), nullable=False)
    entity_id = db.Column(db.Integer, nullable=False)
    changed_at = db.Column(db.DateTime, nullable=False)
    change_set_id = db.Column(db.BigInteger, nullable=True)
    # SQL NULL is a source removal. No HR notes or contact fields are journaled.
    state = db.Column(db.JSON(none_as_null=True), nullable=True)


class StaffingRosterClose(db.Model):
    __tablename__ = "staffing_roster_closes"
    __table_args__ = (
        db.CheckConstraint("opens_at < closes_at", name="ck_staffing_roster_close_window"),
        db.Index("ix_staffing_roster_close_gateway_date", "gateway_code", "sort_date"),
    )

    # Captured source identities are retained without cascading source FKs.
    sort_date_operation_id = db.Column(db.Integer, primary_key=True, autoincrement=False)
    gateway_code = db.Column(db.String(8), nullable=False)
    sort_name = db.Column(db.String(32), nullable=False)
    sort_date = db.Column(db.Date, nullable=False)
    sort_unit_id = db.Column(db.Integer, nullable=False)
    opens_at = db.Column(db.DateTime, nullable=False)
    closes_at = db.Column(db.DateTime, nullable=False)
    sealed_at = db.Column(db.DateTime, nullable=True)
    unavailable_at = db.Column(db.DateTime, nullable=True)


class StaffingRosterPlacement(db.Model):
    __tablename__ = "staffing_roster_placements"
    __table_args__ = (db.Index("ix_staffing_roster_placement_area", "work_area_id", "sort_date_operation_id"),)

    sort_date_operation_id = db.Column(db.Integer, db.ForeignKey("staffing_roster_closes.sort_date_operation_id"), primary_key=True)
    person_id = db.Column(db.Integer, primary_key=True)
    work_area_id = db.Column(db.Integer, primary_key=True)
    # Immutable reporting labels. Tree moves and employee edits leave these intact.
    area_path = db.Column(db.String(600), nullable=False)
    employee_id = db.Column(db.String(80), nullable=False)
    employee_name = db.Column(db.String(161), nullable=False)
    classification = db.Column(db.String(40), nullable=False)


class StaffingAttendanceOutcome(db.Model):
    __tablename__ = "staffing_attendance_outcomes"
    __table_args__ = (
        db.CheckConstraint(
            "status IS NULL OR status IN (" + ", ".join(repr(value) for value in STAFFING_DAILY_ATTENDANCE_STATUSES) + ")",
            name="ck_staffing_attendance_outcome_status",
        ),
        db.CheckConstraint(
            "source_kind IN ('unknown', 'live', 'daily_attendance', 'timecard')",
            name="ck_staffing_attendance_outcome_source",
        ),
        db.Index("ix_staffing_attendance_outcome_scope", "gateway_code", "work_area_id", "sort_date"),
        db.Index("ix_staffing_attendance_outcome_person", "person_id", "sort_date"),
        db.Index("ix_staffing_attendance_outcome_retention", "gateway_code", "sort_date", "sort_date_operation_id", "person_id"),
    )

    sort_date_operation_id = db.Column(db.Integer, primary_key=True, autoincrement=False)
    person_id = db.Column(db.Integer, primary_key=True)
    gateway_code = db.Column(db.String(8), nullable=False)
    sort_name = db.Column(db.String(32), nullable=False)
    sort_unit_id = db.Column(db.Integer, nullable=False)
    sort_date = db.Column(db.Date, nullable=False)
    work_area_id = db.Column(db.Integer, nullable=False)
    employee_id = db.Column(db.String(80), nullable=False)
    employee_name = db.Column(db.String(161), nullable=False)
    classification = db.Column(db.String(40), nullable=True)
    # NULL records an explicit clear and suppresses an older source fallback.
    status = db.Column(db.String(24), nullable=True)
    recorded_at = db.Column(db.DateTime, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False)
    source_kind = db.Column(db.String(20), nullable=False, server_default="unknown")
    source_record_id = db.Column(db.Integer, nullable=True)
