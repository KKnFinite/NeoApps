from datetime import datetime
from app.extensions import db


class NeoSubZeroAuditEntry(db.Model):
    """Append-only operational/reporting corrections, never a second state machine."""
    __tablename__ = 'neosubzero_audit_entries'
    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(db.Integer, db.ForeignKey('sort_date_operations.id'), nullable=False, index=True)
    mission_id = db.Column(db.Integer, db.ForeignKey('sort_date_missions.id'), nullable=True)
    tail_number = db.Column(db.String(32), nullable=True)
    event_id = db.Column(db.Integer, db.ForeignKey('neosubzero_departure_deice_events.id'), nullable=True)
    spray_record_id = db.Column(db.Integer, db.ForeignKey('neosubzero_spray_records.id'), nullable=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    action = db.Column(db.String(64), nullable=False)
    reason = db.Column(db.String(500), nullable=True)
    before_values = db.Column(db.JSON, nullable=True)
    after_values = db.Column(db.JSON, nullable=True)
    recorded_at_utc = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
