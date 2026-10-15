"""Sort-scoped driver identity; never an aircraft/resource assignment."""
from datetime import datetime
from app.extensions import db


class NeoScorpionTruckDriver(db.Model):
    __tablename__ = "neoscorpion_truck_drivers"
    __table_args__ = (
        db.UniqueConstraint("sort_date_operation_id", "fuel_truck_id", name="uq_scorpion_driver_sort_truck"),
        db.UniqueConstraint("sort_date_operation_id", "driver_user_id", name="uq_scorpion_driver_sort_user"),
    )
    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(db.Integer, db.ForeignKey("sort_date_operations.id"), nullable=False)
    fuel_truck_id = db.Column(db.Integer, db.ForeignKey("neoscorpion_fuel_trucks.id"), nullable=False)
    driver_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    manual_name = db.Column(db.String(120), nullable=False, default='', server_default='')
    changed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    driver = db.relationship("User", foreign_keys=[driver_user_id])


class NeoScorpionTruckDriverAudit(db.Model):
    __tablename__ = "neoscorpion_truck_driver_audits"
    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(db.Integer, db.ForeignKey("sort_date_operations.id"), nullable=False, index=True)
    fuel_truck_id = db.Column(db.Integer, db.ForeignKey("neoscorpion_fuel_trucks.id"), nullable=False)
    previous_driver_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    driver_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    changed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    reason = db.Column(db.String(240), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
