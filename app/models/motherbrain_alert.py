from datetime import datetime

from app.extensions import db


class MotherBrainAlert(db.Model):
    __tablename__ = "motherbrain_alerts"

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=False, index=True)
    gateway_code = db.Column(db.String(8), nullable=False, index=True)
    scope = db.Column(db.String(32), nullable=False, default="motherbrain", index=True)
    alert_key = db.Column(db.String(160), nullable=False, default="", index=True)
    severity = db.Column(db.String(16), nullable=False, default="info", index=True)
    title = db.Column(db.String(120), nullable=False)
    message = db.Column(db.Text, nullable=False, default="")
    related_url = db.Column(db.String(255), nullable=False, default="")
    related_label = db.Column(db.String(80), nullable=False, default="")
    permission_key = db.Column(db.String(120), nullable=False, default="", index=True)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    acknowledged = db.Column(db.Boolean, nullable=False, default=False, index=True)
    acknowledged_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gateway = db.relationship("Gateway")
