from datetime import datetime

from app.extensions import db


class UserToken(db.Model):
    __tablename__ = "user_tokens"
    __table_args__ = (
        db.CheckConstraint(
            "token_type IN ('email_verification', 'password_reset')",
            name="ck_user_tokens_token_type",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    token_hash = db.Column(db.String(128), nullable=False, unique=True, index=True)
    token_type = db.Column(db.String(32), nullable=False, index=True)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("User", backref="tokens")

    @property
    def is_used(self):
        return self.used_at is not None

    def is_expired(self, now=None):
        now = now or datetime.utcnow()
        return self.expires_at <= now
