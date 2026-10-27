"""Small immutable model metadata and append-only approval/rollback history."""
from datetime import datetime
from app.extensions import db


class FrostModelArtifact(db.Model):
    __tablename__ = 'frost_model_artifacts'
    __table_args__ = (db.UniqueConstraint('gateway_id', 'model_version', name='uq_frost_model_version'),)
    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey('gateways.id'), nullable=False, index=True)
    model_version = db.Column(db.String(64), nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    vault_key = db.Column(db.String(512), nullable=False)
    status = db.Column(db.String(24), nullable=False)
    metadata_summary = db.Column(db.JSON, nullable=False)
    imported_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    imported_at_utc = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class FrostModelRelease(db.Model):
    __tablename__ = 'frost_model_releases'
    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey('gateways.id'), nullable=False, index=True)
    artifact_id = db.Column(db.Integer, db.ForeignKey('frost_model_artifacts.id'), nullable=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    action = db.Column(db.String(16), nullable=False)
    reason = db.Column(db.String(240), nullable=False)
    recorded_at_utc = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
