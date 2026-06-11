from datetime import datetime

from app.extensions import db


class NeoErmacDoorPull(db.Model):
    __tablename__ = "neoermac_door_pulls"

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=False, index=True)
    sort_date_operation_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_operations.id"),
        nullable=True,
        index=True,
    )
    door = db.Column(db.String(8), nullable=False, index=True)
    destination = db.Column(db.String(8), nullable=False, index=True)
    actual_pure_pull_time_local = db.Column(db.Time, nullable=True)
    no_pure_pull = db.Column(db.Boolean, nullable=False, default=False)
    actual_first_mix_pull_time_local = db.Column(db.Time, nullable=True)
    no_first_mix_pull = db.Column(db.Boolean, nullable=False, default=False)
    actual_second_mix_pull_time_local = db.Column(db.Time, nullable=True)
    no_second_mix_pull = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gateway = db.relationship("Gateway")
    sort_date_operation = db.relationship("SortDateOperation")
