from datetime import datetime
from app.extensions import db


class NeoSubZeroOperationContext(db.Model):
    __tablename__ = 'neosubzero_operation_contexts'
    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(db.Integer, db.ForeignKey('sort_date_operations.id'), nullable=False, unique=True)
    reason_for_application = db.Column(db.String(120), nullable=True)
    active_precipitation = db.Column(db.String(120), nullable=True)
    ambient_temperature = db.Column(db.String(32), nullable=True)
    dew_point = db.Column(db.String(32), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
