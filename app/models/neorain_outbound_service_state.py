"""Rain-owned current-sort outbound support fields, separate from flight authority."""
from datetime import datetime

from app.extensions import db


class NeoRainOutboundServiceState(db.Model):
    __tablename__ = "neorain_outbound_service_states"
    __table_args__ = (
        db.UniqueConstraint(
            "sort_date_mission_id", name="uq_neorain_outbound_service_mission",
        ),
        db.CheckConstraint(
            "jump_count IS NULL OR (jump_count >= 0 AND jump_count <= 9)",
            name="ck_neorain_outbound_jump_single_digit",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(
        db.Integer, db.ForeignKey("sort_date_operations.id"), nullable=False, index=True
    )
    sort_date_mission_id = db.Column(
        db.Integer, db.ForeignKey("sort_date_missions.id"), nullable=False, index=True
    )
    meal = db.Column(db.Boolean, nullable=False, default=False, server_default="false")
    jump_count = db.Column(db.Integer, nullable=True)
    js_in_time = db.Column(db.Time, nullable=True)
    revision = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    operation = db.relationship("SortDateOperation")
    mission = db.relationship("SortDateMission")
    updated_by = db.relationship("User")
