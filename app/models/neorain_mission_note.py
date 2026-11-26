from datetime import datetime

from app.extensions import db
from sqlalchemy.orm import synonym


class NeoRainMissionNote(db.Model):
    """A note belongs to a canonical mission and its Sort through the mission FK."""

    __tablename__ = "neorain_mission_notes"

    id = db.Column(db.Integer, primary_key=True)
    sort_date_mission_id = db.Column(
        db.Integer, db.ForeignKey("sort_date_missions.id"), nullable=False, index=True
    )
    text = db.Column(db.Text, nullable=False)
    notes = synonym("text")
    minutes = db.Column(db.Integer)
    code = db.Column(db.String(2))
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    version = db.Column(db.Integer, nullable=False, default=1)
