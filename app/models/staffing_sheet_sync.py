"""One durable, payload-free delivery receipt for the isolated TEST workbook."""
from datetime import datetime
from app.extensions import db


class StaffingSheetSync(db.Model):
    __tablename__ = 'staffing_sheet_sync'
    __table_args__ = (db.CheckConstraint('id = 1', name='ck_staffing_sheet_sync_singleton'),)
    id = db.Column(db.Integer, primary_key=True)
    requested_revision = db.Column(db.Integer, nullable=False, default=1)
    delivered_revision = db.Column(db.Integer, nullable=False, default=0)
    retry_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    lease_until = db.Column(db.DateTime)
    lease_token = db.Column(db.String(32))
    attempts = db.Column(db.Integer, nullable=False, default=0)
    last_error = db.Column(db.String(100))
    verified_at = db.Column(db.DateTime)
    content_checksum = db.Column(db.String(64))
