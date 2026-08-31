from datetime import datetime

from app.extensions import db


class NeoSubZeroSetting(db.Model):
    __tablename__ = "neosubzero_settings"
    __table_args__ = (
        db.UniqueConstraint("gateway_id", name="uq_neosubzero_setting_gateway"),
        db.CheckConstraint(
            "type_i_concentration_percent BETWEEN 1 AND 100",
            name="ck_neosubzero_type_i_concentration",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(
        db.Integer,
        db.ForeignKey("gateways.id"),
        nullable=False,
        index=True,
    )
    type_i_fluid_name = db.Column(db.String(80), nullable=False, default="Type I")
    type_i_concentration_percent = db.Column(db.Integer, nullable=False, default=50)
    type_iv_fluid_name = db.Column(db.String(80), nullable=False, default="Type IV")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gateway = db.relationship("Gateway")
