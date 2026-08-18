from datetime import datetime

from app.extensions import db


class LiveScreenRefreshSetting(db.Model):
    __tablename__ = "live_screen_refresh_settings"
    __table_args__ = (
        db.UniqueConstraint(
            "gateway_id",
            "screen_key",
            name="uq_live_screen_refresh_setting_gateway_screen",
        ),
        db.CheckConstraint(
            "interval_seconds IN (0, 5, 10, 15, 30, 60)",
            name="ck_live_screen_refresh_setting_interval",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(
        db.Integer,
        db.ForeignKey("gateways.id"),
        nullable=False,
    )
    screen_key = db.Column(db.String(120), nullable=False)
    interval_seconds = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gateway = db.relationship("Gateway")
