from datetime import datetime

from app.extensions import db


class NeoSubZeroUserPreference(db.Model):
    __tablename__ = "neosubzero_user_preferences"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            name="uq_neosubzero_user_preference_user",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    weather_animations_enabled = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    user = db.relationship("User")
