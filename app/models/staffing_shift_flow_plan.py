from datetime import datetime

from app.extensions import db


class StaffingShiftFlowPlan(db.Model):
    """Optional, complete Shift Flow plan attached to one employee."""

    __tablename__ = "staffing_shift_flow_plans"
    __table_args__ = (
        db.CheckConstraint(
            "ballmat_transition IS NULL OR ballmat_transition IN (1, 2, 3)",
            name="ck_staffing_shift_flow_plans_ballmat_transition",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    staffing_person_id = db.Column(
        db.Integer, db.ForeignKey("staffing_people.id"), nullable=False, unique=True, index=True
    )
    setup_work_area_id = db.Column(db.Integer, db.ForeignKey("staffing_units.id"), index=True)
    sort_start_work_area_id = db.Column(
        db.Integer, db.ForeignKey("staffing_units.id"), nullable=False, index=True
    )
    ballmat_transition = db.Column(db.Integer, nullable=True)
    final_door_work_area_id = db.Column(
        db.Integer, db.ForeignKey("staffing_units.id"), nullable=False, index=True
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    person = db.relationship("StaffingPerson", back_populates="shift_flow_plan")
    setup_work_area = db.relationship("StaffingUnit", foreign_keys=[setup_work_area_id])
    sort_start_work_area = db.relationship("StaffingUnit", foreign_keys=[sort_start_work_area_id])
    final_door_work_area = db.relationship("StaffingUnit", foreign_keys=[final_door_work_area_id])
