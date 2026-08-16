from datetime import datetime

from app.extensions import db


class StaffingGroup(db.Model):
    __tablename__ = "staffing_groups"
    __table_args__ = (
        db.UniqueConstraint("name", name="uq_staffing_groups_name"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(140), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    memberships = db.relationship(
        "StaffingGroupMembership",
        back_populates="group",
        cascade="all, delete-orphan",
        order_by="StaffingGroupMembership.id",
    )
