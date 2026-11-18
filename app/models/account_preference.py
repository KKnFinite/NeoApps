"""Optional presentation preferences; never an authorization source."""
from app.extensions import db


class AccountPreference(db.Model):
    __tablename__ = "account_preferences"
    __table_args__ = (
        db.CheckConstraint("preferred_app IS NULL OR preferred_app IN ('neogateway','neostaffing')", name="ck_account_preference_app"),
        db.CheckConstraint("version > 0", name="ck_account_preference_version"),
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    preferred_app = db.Column(db.String(32), nullable=True)
    show_cryocheck = db.Column(db.Boolean, nullable=False, default=False)
    version = db.Column(db.Integer, nullable=False, default=1)
