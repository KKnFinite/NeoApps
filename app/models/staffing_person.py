from datetime import datetime

from app.extensions import db


STAFFING_WRITABLE_CLASSIFICATIONS = (
    "part_time",
    "full_time_combo",
    "part_time_supervisor",
    "full_time_supervisor",
    "twenty_c_full_time_supervisor",
    "full_time_specialist",
    "manager",
    "division_manager",
)

STAFFING_PHASE1_CLASSIFICATIONS = (
    "seasonal",
    "domiciled_full_time_combo",
    "non_domiciled_full_time_combo",
)

STAFFING_DATABASE_CLASSIFICATIONS = (
    *STAFFING_WRITABLE_CLASSIFICATIONS,
    *STAFFING_PHASE1_CLASSIFICATIONS,
)

# Backward-compatible Phase 1 contract: existing callers use this name for
# normal mutation choices, which must not expose the newly database-valid
# classifications until the later write-enablement deployment.
STAFFING_CLASSIFICATIONS = STAFFING_WRITABLE_CLASSIFICATIONS

STAFFING_EMPLOYEE_STATUSES = (
    "active",
    "disability",
    "comp",
    "military",
    "fmla",
)


class StaffingPerson(db.Model):
    __tablename__ = "staffing_people"
    __table_args__ = (
        db.CheckConstraint(
            "classification IN ('part_time', 'full_time_combo', 'part_time_supervisor', "
            "'full_time_supervisor', 'twenty_c_full_time_supervisor', "
            "'full_time_specialist', 'manager', 'division_manager', "
            "'seasonal', 'domiciled_full_time_combo', "
            "'non_domiciled_full_time_combo')",
            name="ck_staffing_people_classification",
        ),
        db.CheckConstraint(
            "employee_status IN ('active', 'disability', 'comp', 'military', 'fmla')",
            name="ck_staffing_people_employee_status",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.String(80), nullable=False, unique=True, index=True)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    seniority_date = db.Column(db.Date, nullable=False, index=True)
    phone_number = db.Column(db.String(40), nullable=True)
    classification = db.Column(db.String(40), nullable=False, index=True)
    employee_status = db.Column(
        db.String(24),
        nullable=False,
        default="active",
        index=True,
    )
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    work_assignment = db.relationship(
        "StaffingWorkAssignment",
        back_populates="person",
        cascade="all, delete-orphan",
        uselist=False,
    )
    shift_flow_plan = db.relationship(
        "StaffingShiftFlowPlan", back_populates="person", uselist=False,
        cascade="all, delete-orphan"
    )
    leadership_assignments = db.relationship(
        "StaffingLeadershipAssignment",
        back_populates="person",
        cascade="all, delete-orphan",
    )
    reporting_relationships = db.relationship(
        "StaffingReportingRelationship",
        foreign_keys="StaffingReportingRelationship.person_id",
        back_populates="person",
    )
    direct_report_relationships = db.relationship(
        "StaffingReportingRelationship",
        foreign_keys="StaffingReportingRelationship.reports_to_person_id",
        back_populates="reports_to_person",
    )
    twenty_c_affiliations = db.relationship(
        "StaffingTwentyCAffiliation",
        foreign_keys="StaffingTwentyCAffiliation.twenty_c_person_id",
        back_populates="twenty_c_person",
    )
    twenty_c_supervisor_affiliations = db.relationship(
        "StaffingTwentyCAffiliation",
        foreign_keys="StaffingTwentyCAffiliation.ft_supervisor_person_id",
        back_populates="ft_supervisor_person",
    )
    daily_attendance_records = db.relationship(
        "StaffingDailyAttendance",
        back_populates="person",
        cascade="all, delete-orphan",
    )
    qualifications = db.relationship(
        "StaffingPersonQualification",
        back_populates="person",
        cascade="all, delete-orphan",
    )

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()
