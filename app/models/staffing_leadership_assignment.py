from datetime import datetime

from app.extensions import db


STAFFING_LEADERSHIP_LEVELS = (
    "work_area",
    "department",
    "operation",
    "sort",
)


class StaffingLeadershipAssignment(db.Model):
    __tablename__ = "staffing_leadership_assignments"
    __table_args__ = (
        db.CheckConstraint(
            "leadership_level IN ('work_area', 'department', 'operation', 'sort')",
            name="ck_staffing_leadership_assignments_level",
        ),
        db.UniqueConstraint(
            "person_id",
            "unit_id",
            "leadership_level",
            name="uq_staffing_leadership_assignments_exact",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_people.id"),
        nullable=False,
        index=True,
    )
    unit_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_units.id"),
        nullable=False,
        index=True,
    )
    leadership_level = db.Column(db.String(40), nullable=False, index=True)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    person = db.relationship("StaffingPerson", back_populates="leadership_assignments")
    unit = db.relationship("StaffingUnit", back_populates="leadership_assignments")
