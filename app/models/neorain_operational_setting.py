from datetime import datetime

from app.extensions import db


class NeoRainOperationalSetting(db.Model):
    __tablename__ = "neorain_operational_settings"
    __table_args__ = (
        db.UniqueConstraint("gateway_id", name="uq_neorain_operational_settings_gateway"),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=False, index=True)
    gateway_code = db.Column(db.String(8), nullable=False, index=True)
    ground_time_threshold_minutes = db.Column(db.Integer, nullable=False, default=120)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    gateway = db.relationship("Gateway")
