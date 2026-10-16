"""First completed fuel publication and value-only review revisions."""
from app.extensions import db


class NeoRainFuelPublication(db.Model):
    __tablename__ = 'neorain_fuel_publications'
    sort_date_mission_id = db.Column(db.Integer, db.ForeignKey('sort_date_missions.id'), primary_key=True)
    sort_date_operation_id = db.Column(db.Integer, db.ForeignKey('sort_date_operations.id'), nullable=False, index=True)
    baseline_neo_fuel_lbs = db.Column(db.Integer)
    baseline_center_fuel_lbs = db.Column(db.Integer)
    neo_fuel_lbs = db.Column(db.Integer)
    center_fuel_lbs = db.Column(db.Integer)
    change_number = db.Column(db.Integer, nullable=False, default=0)
