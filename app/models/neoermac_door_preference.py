from datetime import datetime

from app.extensions import db


class NeoErmacDoorPreference(db.Model):
    """Saved Door View navigation, independent of operational sort lifetime."""

    __tablename__ = "neoermac_door_preferences"
    __table_args__ = (
        db.UniqueConstraint("user_id", "gateway_id", name="uq_neoermac_door_preferences_user_gateway"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id", ondelete="CASCADE"), nullable=False)
    selected_doors_json = db.Column(db.Text, nullable=False, default="[]")
    active_door = db.Column(db.String(8), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
