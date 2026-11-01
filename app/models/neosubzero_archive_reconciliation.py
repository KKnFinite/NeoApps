"""Discovery metadata only; never part of immutable spray/archive payloads."""
from app.extensions import db


class NeoSubZeroArchiveCursor(db.Model):
    __tablename__ = 'neosubzero_archive_cursors'
    gateway_id = db.Column(db.Integer, db.ForeignKey('gateways.id', ondelete='CASCADE'), primary_key=True)
    operation_after = db.Column(db.Integer, nullable=False, default=0)
    sweep_until = db.Column(db.Integer, nullable=False, default=0)
    audit_after = db.Column(db.Integer, nullable=False, default=0)


class NeoSubZeroArchiveCheckpoint(db.Model):
    __tablename__ = 'neosubzero_archive_checkpoints'
    __table_args__ = (db.Index('ix_subzero_archive_reconcile_retry', 'gateway_id', 'retry_at'),)
    operation_id = db.Column(db.Integer, db.ForeignKey('sort_date_operations.id', ondelete='CASCADE'), primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey('gateways.id', ondelete='CASCADE'), nullable=False)
    source_fingerprint = db.Column(db.String(64))
    attempted_fingerprint = db.Column(db.String(64))
    attempts = db.Column(db.Integer, nullable=False, default=0)
    retry_at = db.Column(db.DateTime)
    last_error = db.Column(db.String(200))
