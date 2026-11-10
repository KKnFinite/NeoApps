"""One independent, durable receipt for Night Shift attendance to Raiden M23."""
from datetime import datetime
from app.extensions import db


class StaffingRaidenSync(db.Model):
    __tablename__ = 'staffing_raiden_sync'
    __table_args__ = (
        db.CheckConstraint('id = 1', name='ck_staffing_raiden_sync_singleton'),
    )

    id = db.Column(db.Integer, primary_key=True)
    requested_revision = db.Column(db.Integer, nullable=False, default=1)
    delivered_revision = db.Column(db.Integer, nullable=False, default=0)
    delivered_operation_id = db.Column(db.Integer)
    delivered_count = db.Column(db.Integer)
    retry_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    lease_until = db.Column(db.DateTime)
    lease_token = db.Column(db.String(32))
    attempts = db.Column(db.Integer, nullable=False, default=0)
    last_error = db.Column(db.String(100))
    verified_at = db.Column(db.DateTime)
