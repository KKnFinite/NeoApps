"""Small processing/index catalog; raw CSV/weather/research rows live in FROST."""
from datetime import datetime
from app.extensions import db


class FrostHistoryTask(db.Model):
    __tablename__ = 'frost_history_tasks'
    __table_args__ = (db.UniqueConstraint('gateway_id','kind','identity','version',name='uq_frost_history_task'),)
    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey('gateways.id'), nullable=False, index=True)
    kind = db.Column(db.String(24), nullable=False)
    identity = db.Column(db.String(32), nullable=False)
    version = db.Column(db.String(64), nullable=False)
    input_hash = db.Column(db.String(64), nullable=True)
    source_key = db.Column(db.String(512), nullable=True)
    result_key = db.Column(db.String(512), nullable=True)
    status = db.Column(db.String(32), nullable=False, default='pending')
    summary = db.Column(db.JSON, nullable=False, default=dict)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    retry_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    lease_token = db.Column(db.String(40), nullable=True)
    last_error = db.Column(db.String(240), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
