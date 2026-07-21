from datetime import datetime

from app.extensions import db


class NeoSektorOperationalSetting(db.Model):
    __tablename__ = "neosektor_operational_settings"
    __table_args__ = (
        db.UniqueConstraint(
            "gateway_id",
            name="uq_neosektor_operational_settings_gateway",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=False, index=True)
    gateway_code = db.Column(db.String(8), nullable=False, index=True)
    first_wave_unload_modifier = db.Column(db.Integer, nullable=False, default=45)
    second_wave_unload_modifier = db.Column(db.Integer, nullable=False, default=37)
    all_up_to_down_minutes = db.Column(db.Integer, nullable=False, default=15)
    google_sheets_compat_enabled = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gateway = db.relationship("Gateway")
