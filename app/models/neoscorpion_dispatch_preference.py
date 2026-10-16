"""Personal Dispatch presentation, independent of operational sorts."""
from datetime import datetime
from app.extensions import db


class NeoScorpionDispatchPreference(db.Model):
    __tablename__ = 'neoscorpion_dispatch_preferences'
    __table_args__ = (
        db.UniqueConstraint('user_id', 'gateway_id', name='uq_scorpion_dispatch_preference_user_gateway'),
        db.CheckConstraint("secondary_sort IN ('etd', 'arrival')", name='ck_scorpion_dispatch_secondary_sort'),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    gateway_id = db.Column(db.Integer, db.ForeignKey('gateways.id', ondelete='CASCADE'), nullable=False)
    arrived_first = db.Column(db.Boolean, nullable=False, default=True)
    secondary_sort = db.Column(db.String(8), nullable=False, default='etd')
    truck_cards_collapsed = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    spear_data_collapsed = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    version = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
