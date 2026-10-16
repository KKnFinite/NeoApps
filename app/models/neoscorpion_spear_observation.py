"""Immutable learning evidence plus a durable, independently delivered outbox."""
from datetime import datetime
from sqlalchemy import event, inspect
from app.extensions import db


class NeoScorpionSpearObservation(db.Model):
    __tablename__ = 'neoscorpion_spear_observations'
    __table_args__ = (
        db.UniqueConstraint('event_key', name='uq_spear_observation_event'),
        db.Index('ix_spear_observation_delivery', 'gateway_id', 'delivered_at', 'retry_at'),
        db.Index('ix_spear_observation_identity', 'assignment_id', 'cycle_number', 'created_at'),
        db.Index('ix_spear_observation_operation', 'operation_id', 'kind', 'created_at'),
    )
    id = db.Column(db.String(32), primary_key=True)
    event_key = db.Column(db.String(64), nullable=False)
    kind = db.Column(db.String(16), nullable=False)
    gateway_id = db.Column(db.Integer, db.ForeignKey('gateways.id'), nullable=False)
    operation_id = db.Column(db.Integer, db.ForeignKey('sort_date_operations.id'), nullable=False)
    mission_id = db.Column(db.Integer, db.ForeignKey('sort_date_missions.id'), nullable=False)
    assignment_id = db.Column(db.Integer, db.ForeignKey('neoscorpion_fuel_assignments.id'), nullable=False)
    cycle_number = db.Column(db.Integer, nullable=False)
    payload = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    retry_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    reconcile_after = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    delivered_at = db.Column(db.DateTime)
    vault_key = db.Column(db.String(512))


@event.listens_for(NeoScorpionSpearObservation, 'before_update')
def protect_observation(_mapper, _connection, row):
    for name in ('event_key', 'kind', 'gateway_id', 'operation_id', 'mission_id',
                 'assignment_id', 'cycle_number', 'payload', 'created_at'):
        if inspect(row).attrs[name].history.has_changes():
            raise ValueError('SPEAR observation evidence is immutable.')
