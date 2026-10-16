"""Gateway-scoped aircraft notes, independent of physical fueling work."""
from app.extensions import db


class NeoScorpionTailNote(db.Model):
    __tablename__ = 'neoscorpion_tail_notes'
    __table_args__ = (db.UniqueConstraint('gateway_id', 'tail_number', name='uq_scorpion_tail_note_gateway_tail'),)
    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey('gateways.id', ondelete='CASCADE'), nullable=False)
    tail_number = db.Column(db.String(32), nullable=False)
    notes = db.Column(db.Text, nullable=False, default='')
    version = db.Column(db.Integer, nullable=False, default=0)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    updated_at_utc = db.Column(db.DateTime, nullable=False)
    editor = db.relationship('User', lazy='joined')
