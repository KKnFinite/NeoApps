from datetime import datetime

from app.extensions import db


STAFFING_CLASSIFICATIONS = (
    "part_time",
    "full_time_combo",
    "part_time_supervisor",
    "full_time_supervisor",
    "full_time_specialist",
    "manager",
    "division_manager",
)


class StaffingPerson(db.Model):
    __tablename__ = "staffing_people"
    __table_args__ = (
        db.CheckConstraint(
            "classification IN ('part_time', 'full_time_combo', 'part_time_supervisor', "
            "'full_time_supervisor', 'full_time_specialist', 'manager', 'division_manager')",
            name="ck_staffing_people_classification",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.String(80), nullable=False, unique=True, index=True)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    seniority_date = db.Column(db.Date, nullable=False, index=True)
    phone_number = db.Column(db.String(40), nullable=True)
    classification = db.Column(db.String(40), nullable=False, index=True)
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
    leadership_assignments = db.relationship(
        "StaffingLeadershipAssignment",
        back_populates="person",
        cascade="all, delete-orphan",
    )

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()
