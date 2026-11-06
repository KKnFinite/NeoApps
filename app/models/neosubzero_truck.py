from datetime import datetime

from app.extensions import db


class NeoSubZeroTruck(db.Model):
    """Gateway asset registry; sort assignments retain their original snapshots."""

    __tablename__ = 'neosubzero_trucks'
    __table_args__ = (db.UniqueConstraint('gateway_id', 'number', name='uq_subzero_registered_truck'),)
    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey('gateways.id'), nullable=False, index=True)
    number = db.Column(db.String(32), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    history = db.Column(db.JSON, nullable=False, default=list)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
