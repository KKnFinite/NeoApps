from datetime import datetime

from app.extensions import db


class NeoRainDelayInfo(db.Model):
    """One operator-entered delay fact for a canonical current-sort mission."""

    __tablename__ = "neorain_delay_info"

    id = db.Column(db.Integer, primary_key=True)
    sort_date_mission_id = db.Column(
        db.Integer, db.ForeignKey("sort_date_missions.id"), nullable=False, index=True
    )
    minutes = db.Column(db.Integer, nullable=False)
    code = db.Column(db.String(2), nullable=False, index=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    mission = db.relationship("SortDateMission", back_populates="delay_info_rows")
