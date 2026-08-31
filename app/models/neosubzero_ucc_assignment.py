from datetime import datetime

from app.extensions import db


class NeoSubZeroUccAssignment(db.Model):
    """One current-sort Driver or Flyer slot in the SubZero UCC."""

    __tablename__ = "neosubzero_ucc_assignments"
    __table_args__ = (
        db.CheckConstraint(
            "ramp IN ('Remote', 'Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo')",
            name="ck_neosubzero_ucc_assignment_ramp",
        ),
        db.CheckConstraint(
            "position_number BETWEEN 1 AND 4",
            name="ck_neosubzero_ucc_assignment_position",
        ),
        db.CheckConstraint(
            "team_role IN ('driver', 'flyer')",
            name="ck_neosubzero_ucc_assignment_role",
        ),
        db.UniqueConstraint(
            "sort_date_operation_id",
            "ramp",
            "position_number",
            "team_role",
            name="uq_neosubzero_ucc_operation_slot",
        ),
        db.UniqueConstraint(
            "sort_date_operation_id",
            "person_id",
            name="uq_neosubzero_ucc_operation_person",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_operations.id"),
        nullable=False,
        index=True,
    )
    ramp = db.Column(db.String(16), nullable=False, index=True)
    position_number = db.Column(db.Integer, nullable=False)
    team_role = db.Column(db.String(16), nullable=False)
    person_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_people.id"),
        nullable=True,
        index=True,
    )
    assigned_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )
    assigned_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    operation = db.relationship("SortDateOperation")
    person = db.relationship("StaffingPerson")
    assigned_by_user = db.relationship("User")
