from datetime import datetime

from app.extensions import db


STAFFING_UNIT_TYPES = ("sort", "operation", "department", "work_area")


class StaffingUnit(db.Model):
    __tablename__ = "staffing_units"
    __table_args__ = (
        db.CheckConstraint(
            "unit_type IN ('sort', 'operation', 'department', 'work_area')",
            name="ck_staffing_units_type",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("staffing_units.id"), nullable=True, index=True)
    unit_type = db.Column(db.String(32), nullable=False, index=True)
    name = db.Column(db.String(140), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    display_order = db.Column(db.Integer, nullable=False, default=0)
    required_headcount = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    parent = db.relationship(
        "StaffingUnit",
        remote_side=[id],
        back_populates="children",
    )
    children = db.relationship(
        "StaffingUnit",
        back_populates="parent",
        cascade="all, delete-orphan",
    )
    work_assignments = db.relationship(
        "StaffingWorkAssignment",
        back_populates="work_area",
        cascade="all, delete-orphan",
    )
    leadership_assignments = db.relationship(
        "StaffingLeadershipAssignment",
        back_populates="unit",
        cascade="all, delete-orphan",
    )

    @property
    def type_label(self):
        return self.unit_type.replace("_", " ").title()
