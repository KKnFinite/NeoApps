from app.extensions import db


class StaffingGroupMembership(db.Model):
    __tablename__ = "staffing_group_memberships"
    __table_args__ = (
        db.UniqueConstraint(
            "group_id",
            "staffing_unit_id",
            name="uq_staffing_group_memberships_group_unit",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    staffing_unit_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_units.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    group = db.relationship("StaffingGroup", back_populates="memberships")
    staffing_unit = db.relationship("StaffingUnit")
