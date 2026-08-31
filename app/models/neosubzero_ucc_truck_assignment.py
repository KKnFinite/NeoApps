from datetime import datetime

from app.extensions import db


class NeoSubZeroUccTruckAssignment(db.Model):
    """Current-sort truck selected for one UCC ramp treatment position."""

    __tablename__ = "neosubzero_ucc_truck_assignments"
    __table_args__ = (
        db.CheckConstraint(
            "ramp IN ('Remote', 'Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo')",
            name="ck_neosubzero_ucc_truck_ramp",
        ),
        db.CheckConstraint(
            "position_number BETWEEN 1 AND 4",
            name="ck_neosubzero_ucc_truck_position",
        ),
        db.UniqueConstraint(
            "sort_date_operation_id",
            "ramp",
            "position_number",
            name="uq_neosubzero_ucc_operation_truck_position",
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
    truck_number = db.Column(db.String(32), nullable=True)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    operation = db.relationship("SortDateOperation")
    updated_by_user = db.relationship("User")
