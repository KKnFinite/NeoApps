"""Dispatcher attestation of pre-sort fuel, never a physical truck visit."""
from app.extensions import db
from sqlalchemy import event


class NeoScorpionDirectEntry(db.Model):
    __tablename__ = 'neoscorpion_direct_entries'
    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey('gateways.id'), nullable=False)
    sort_date_operation_id = db.Column(db.Integer, db.ForeignKey('sort_date_operations.id'), nullable=False, index=True)
    sort_date_mission_id = db.Column(db.Integer, db.ForeignKey('sort_date_missions.id'), nullable=False, unique=True)
    tail_number = db.Column(db.String(32), nullable=False)
    aircraft_type = db.Column(db.String(24), nullable=False)
    request_key = db.Column(db.String(36), nullable=False, unique=True)
    request_fingerprint = db.Column(db.String(64), nullable=False)
    completed_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    completed_at_utc = db.Column(db.DateTime, nullable=False)
    snapshot = db.Column(db.JSON, nullable=False)
    completed_by = db.relationship('User')


@event.listens_for(NeoScorpionDirectEntry, 'before_update')
@event.listens_for(NeoScorpionDirectEntry, 'before_delete')
def _immutable_entry(mapper, connection, target):
    raise ValueError('Dispatcher direct-entry evidence is immutable; use an audited correction workflow.')
