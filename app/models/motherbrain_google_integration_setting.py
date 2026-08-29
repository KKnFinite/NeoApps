from datetime import datetime

from app.extensions import db


class MotherBrainGoogleIntegrationSetting(db.Model):
    __tablename__ = "motherbrain_google_integration_settings"
    __table_args__ = (
        db.UniqueConstraint(
            "gateway_id",
            "sort_name",
            name="uq_motherbrain_google_integration_gateway_sort",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(
        db.Integer,
        db.ForeignKey("gateways.id"),
        nullable=False,
        index=True,
    )
    gateway_code = db.Column(db.String(8), nullable=False, index=True)
    sort_name = db.Column(db.String(32), nullable=False, index=True)
    live_polling_enabled = db.Column(db.Boolean, nullable=False, default=False)
    rain_integration_mode = db.Column(
        db.String(40),
        nullable=False,
        default="google_primary",
        server_default="google_primary",
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gateway = db.relationship("Gateway")
