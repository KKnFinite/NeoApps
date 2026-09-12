"""Accountability configuration and resolution identities, never running counts."""
from datetime import datetime

from app.extensions import db


class StaffingTrackerSetting(db.Model):
    __tablename__ = "staffing_tracker_settings"
    unit_id = db.Column(db.Integer, db.ForeignKey("staffing_units.id"), primary_key=True)
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    policy_json = db.Column(db.Text, nullable=True)
    version = db.Column(db.Integer, nullable=False, default=0)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class StaffingComboWorkday(db.Model):
    __tablename__ = "staffing_combo_workdays"
    __table_args__ = (db.CheckConstraint("first_sort_id <> second_sort_id", name="ck_staffing_combo_distinct_sorts"),)
    person_id = db.Column(db.Integer, db.ForeignKey("staffing_people.id", ondelete="CASCADE"), primary_key=True)
    first_sort_id = db.Column(db.Integer, db.ForeignKey("staffing_units.id"), nullable=False)
    second_sort_id = db.Column(db.Integer, db.ForeignKey("staffing_units.id"), nullable=False)
    version = db.Column(db.Integer, nullable=False, default=0)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class StaffingAccountabilityWorkday(db.Model):
    """Frozen grouping of real source operations, or an explicitly imported fact.

    Native status is derived from retained StaffingAttendanceOccurrence sources.
    Config snapshots prevent later workday settings from regrouping history.
    """
    __tablename__ = "staffing_accountability_workdays"
    __table_args__ = (
        db.Index("ix_staffing_accountability_workday_person_date", "person_id", "workday_date"),
        db.CheckConstraint("imported_status IS NULL OR imported_status IN ('call_in', 'no_call')",
                           name="ck_staffing_accountability_import_status"),
    )
    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(db.Integer, db.ForeignKey("staffing_people.id", ondelete="CASCADE"), nullable=False)
    workday_date = db.Column(db.Date, nullable=False, index=True)
    first_sort_id = db.Column(db.Integer, db.ForeignKey("staffing_units.id"))
    second_sort_id = db.Column(db.Integer, db.ForeignKey("staffing_units.id"))
    requires_pair = db.Column(db.Boolean, nullable=False, default=False)
    imported_status = db.Column(db.String(16))
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class StaffingAccountabilitySource(db.Model):
    __tablename__ = "staffing_accountability_sources"
    __table_args__ = (
        db.UniqueConstraint("person_id", "operation_id", name="uq_staffing_accountability_source"),
        db.UniqueConstraint("workday_id", "position", name="uq_staffing_accountability_position"),
        db.CheckConstraint("position IN (1, 2)", name="ck_staffing_accountability_position"),
    )
    id = db.Column(db.Integer, primary_key=True)
    workday_id = db.Column(db.Integer, db.ForeignKey("staffing_accountability_workdays.id", ondelete="CASCADE"), nullable=False, index=True)
    person_id = db.Column(db.Integer, db.ForeignKey("staffing_people.id", ondelete="CASCADE"), nullable=False)
    operation_id = db.Column(db.Integer, db.ForeignKey("sort_date_operations.id"), nullable=False)
    position = db.Column(db.Integer, nullable=False)


class StaffingAccountabilityResolution(db.Model):
    __tablename__ = "staffing_accountability_resolutions"
    __table_args__ = (
        db.CheckConstraint("kind IN ('informal', 'issue', 'override', 'no_discipline', 'history')", name="ck_staffing_accountability_resolution_kind"),
        db.CheckConstraint("(kind = 'informal' AND action IN ('Verbal', 'Written Warning') AND trigger_workday_id IS NULL) OR "
                           "(kind = 'history' AND action IN ('Warning Letter', 'Suspension', 'Termination') AND trigger_workday_id IS NULL) OR "
                           "(kind IN ('issue', 'override') AND action IN ('Warning Letter', 'Suspension', 'Termination') AND trigger_workday_id IS NOT NULL) OR "
                           "(kind = 'no_discipline' AND action IS NULL AND trigger_workday_id IS NOT NULL)",
                           name="ck_staffing_accountability_resolution_action"),
        db.UniqueConstraint("trigger_workday_id", name="uq_staffing_accountability_formal_trigger"),
        db.CheckConstraint("kind = 'no_discipline' OR action IS NOT NULL", name="ck_staffing_accountability_action_required"),
    )
    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(db.Integer, db.ForeignKey("staffing_people.id", ondelete="CASCADE"), nullable=False, index=True)
    trigger_workday_id = db.Column(db.Integer, db.ForeignKey("staffing_accountability_workdays.id", ondelete="CASCADE"))
    kind = db.Column(db.String(20), nullable=False)
    recommendation = db.Column(db.String(32))
    action = db.Column(db.String(32))
    note = db.Column(db.Text)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    resolved_on = db.Column(db.Date, nullable=False, index=True)
    resolved_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)


class StaffingAccountabilityCoverage(db.Model):
    """Exact informal delivery coverage; later finalized days stay unresolved."""
    __tablename__ = "staffing_accountability_coverage"
    workday_id = db.Column(db.Integer, db.ForeignKey("staffing_accountability_workdays.id", ondelete="CASCADE"), primary_key=True)
    resolution_id = db.Column(db.Integer, db.ForeignKey("staffing_accountability_resolutions.id", ondelete="CASCADE"), nullable=False, index=True)


class StaffingAccountabilityReconciliation(db.Model):
    __tablename__ = "staffing_accountability_reconciliations"
    person_id = db.Column(db.Integer, db.ForeignKey("staffing_people.id", ondelete="CASCADE"), primary_key=True)
    complete_since = db.Column(db.Date, nullable=False)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    reconciled_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
