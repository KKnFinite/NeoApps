from datetime import datetime

from app.extensions import db


class NeoRainGoogleFuelValue(db.Model):
    """Last bounded legacy Google fuel display value for one Rain mission."""

    __tablename__ = "neorain_google_fuel_values"
    __table_args__ = (
        db.UniqueConstraint("sort_date_mission_id", name="uq_neorain_google_fuel_mission"),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(db.Integer, db.ForeignKey("sort_date_operations.id"), nullable=False, index=True)
    sort_date_mission_id = db.Column(db.Integer, db.ForeignKey("sort_date_missions.id"), nullable=False, index=True)
    neo_fuel = db.Column(db.String(64), nullable=True)
    center_fuel = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    operation = db.relationship("SortDateOperation")
    mission = db.relationship("SortDateMission")
