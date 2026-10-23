from datetime import datetime
from app.extensions import db


class NeoSubZeroArchiveDelivery(db.Model):
    """Delivery receipts only; canonical spray/audit rows remain the source."""
    __tablename__ = 'neosubzero_archive_deliveries'
    __table_args__ = (db.UniqueConstraint('operation_id', 'source_id', name='uq_subzero_archive_source'),)
    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey('gateways.id'), nullable=False, index=True)
    operation_id = db.Column(db.Integer, db.ForeignKey('sort_date_operations.id'), nullable=False, index=True)
    source_id = db.Column(db.String(80), nullable=False)
    content_checksum = db.Column(db.String(64), nullable=False)
    delivered_checksum = db.Column(db.String(64), nullable=True)
    vault_key = db.Column(db.String(512), nullable=True)
    learning_key = db.Column(db.String(512), nullable=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    retry_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    delivered_at = db.Column(db.DateTime, nullable=True)
    last_error = db.Column(db.String(200), nullable=True)
