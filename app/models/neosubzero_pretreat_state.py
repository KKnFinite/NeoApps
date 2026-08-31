from datetime import datetime
from app.extensions import db


class NeoSubZeroPretreatState(db.Model):
    __tablename__ = "neosubzero_pretreat_states"
    __table_args__ = (db.UniqueConstraint("sort_date_operation_id", "tail_number", name="uq_neosubzero_pretreat_operation_tail"),)
    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(db.Integer, db.ForeignKey("sort_date_operations.id"), nullable=False, index=True)
    tail_number = db.Column(db.String(32), nullable=False, index=True)
    pretreat_planned = db.Column(db.Boolean, nullable=False, default=False)
    configured_at_utc = db.Column(db.DateTime, nullable=True)
    pass1_surface_area = db.Column(db.String(32), nullable=True)
    pass1_started_at_utc = db.Column(db.DateTime, nullable=True)
    pass1_ended_at_utc = db.Column(db.DateTime, nullable=True)
    pass2_surface_area = db.Column(db.String(32), nullable=True)
    pass2_started_at_utc = db.Column(db.DateTime, nullable=True)
    pass2_ended_at_utc = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    operation = db.relationship("SortDateOperation")
