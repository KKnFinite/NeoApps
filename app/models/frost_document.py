"""FROST's private source catalog/outbox, not live treatment records."""
from datetime import datetime
from app.extensions import db


class FrostDocument(db.Model):
    __tablename__ = 'frost_documents'
    __table_args__ = (db.UniqueConstraint('gateway_id','sha256',name='uq_frost_gateway_document_hash'),)
    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey('gateways.id'), nullable=False, index=True)
    original_filename = db.Column(db.String(240), nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    byte_length = db.Column(db.Integer, nullable=False)
    imported_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    imported_at_utc = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    document_type = db.Column(db.String(64), nullable=False)
    extraction_status = db.Column(db.String(32), nullable=False)
    extraction_summary = db.Column(db.JSON, nullable=False)
    date_start = db.Column(db.Date, nullable=True)
    date_end = db.Column(db.Date, nullable=True)
    # Encrypted staging survives an R2 outage/restart; removed only after read-back.
    staged_bytes = db.Column(db.LargeBinary, nullable=True)
    original_key = db.Column(db.String(512), nullable=True)
    extracted_key = db.Column(db.String(512), nullable=True)
    manifest_key = db.Column(db.String(512), nullable=True)
    archived_at_utc = db.Column(db.DateTime, nullable=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    retry_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    last_error = db.Column(db.String(240), nullable=True)


class FrostDocumentAudit(db.Model):
    __tablename__ = 'frost_document_audits'
    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey('frost_documents.id'), nullable=False, index=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    action = db.Column(db.String(40), nullable=False)
    recorded_at_utc = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    details = db.Column(db.JSON, nullable=False)
