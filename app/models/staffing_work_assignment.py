from datetime import datetime

from app.extensions import db


class StaffingWorkAssignment(db.Model):
    __tablename__ = "staffing_work_assignments"
    __table_args__ = (
        db.UniqueConstraint("person_id", name="uq_staffing_work_assignments_person"),
    )

    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_people.id"),
        nullable=False,
        index=True,
    )
    work_area_unit_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_units.id"),
        nullable=False,
        index=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    person = db.relationship("StaffingPerson", back_populates="work_assignment")
    work_area = db.relationship("StaffingUnit", back_populates="work_assignments")
