from datetime import datetime
from app.extensions import db


class NeoRainMissionOverride(db.Model):
    __tablename__ = 'neorain_mission_overrides'
    __table_args__ = (db.UniqueConstraint('sort_date_mission_id', 'field', name='uq_rain_mission_field'),)
    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey('gateways.id'), nullable=False)
    sort_date_operation_id = db.Column(db.Integer, db.ForeignKey('sort_date_operations.id'), nullable=False, index=True)
    sort_date_mission_id = db.Column(db.Integer, db.ForeignKey('sort_date_missions.id'), nullable=False, index=True)
    field = db.Column(db.String(32), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=False)
    value = db.Column(db.JSON, nullable=True)
    upstream = db.Column(db.JSON, nullable=False, default=dict)
    generation = db.Column(db.Integer, nullable=False, default=0)
    source = db.Column(db.String(32), nullable=False, default='neorain_operator')
    modified_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    modified_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    sync_status = db.Column(db.String(24), nullable=False, default='NOT_WRITTEN')
    sync_detail = db.Column(db.String(300), nullable=True)
    sync_attempted_at = db.Column(db.DateTime, nullable=True)


class NeoRainOverrideAudit(db.Model):
    __tablename__ = 'neorain_override_audits'
    __table_args__ = (db.UniqueConstraint('override_id', 'generation', 'action', name='uq_rain_override_audit'),)
    id = db.Column(db.Integer, primary_key=True)
    override_id = db.Column(db.Integer, db.ForeignKey('neorain_mission_overrides.id'), nullable=False, index=True)
    generation = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String(16), nullable=False)
    actor_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    before_value = db.Column(db.JSON, nullable=True)
    after_value = db.Column(db.JSON, nullable=True)
    detail = db.Column(db.String(300), nullable=True)
