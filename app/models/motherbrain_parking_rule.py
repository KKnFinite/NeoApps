from datetime import datetime

from app.extensions import db


class MotherBrainParkingRule(db.Model):
    __tablename__ = "motherbrain_parking_rules"
    __table_args__ = (
        db.UniqueConstraint(
            "gateway_id",
            "rule_category",
            "subject_type",
            "subject_value",
            "ramp_code",
            "rule_behavior",
            name="uq_motherbrain_parking_rule_identity",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=False, index=True)
    gateway_code = db.Column(db.String(8), nullable=False, index=True)
    rule_category = db.Column(db.String(48), nullable=False, index=True)
    subject_type = db.Column(db.String(32), nullable=False, index=True)
    subject_value = db.Column(db.String(32), nullable=False, index=True)
    ramp_code = db.Column(db.String(16), nullable=False, index=True)
    rule_behavior = db.Column(db.String(24), nullable=False, index=True)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    note = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gateway = db.relationship("Gateway")


class MotherBrainParkingSettings(db.Model):
    __tablename__ = "motherbrain_parking_settings"
    __table_args__ = (
        db.UniqueConstraint("gateway_id", name="uq_motherbrain_parking_settings_gateway"),
        db.CheckConstraint(
            "deice_spacing_threshold_minutes >= 0",
            name="ck_motherbrain_parking_settings_deice_nonnegative",
        ),
        db.CheckConstraint(
            "preferred_max_per_ramp IS NULL OR preferred_max_per_ramp >= 0",
            name="ck_motherbrain_parking_settings_preferred_max_nonnegative",
        ),
        db.CheckConstraint(
            "inbound_same_ramp_spacing_minutes >= 0",
            name="ck_motherbrain_parking_settings_inbound_spacing_nonnegative",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=False, index=True)
    gateway_code = db.Column(db.String(8), nullable=False, index=True)
    include_remote_default = db.Column(db.Boolean, nullable=False, default=False)
    include_throat_default = db.Column(db.Boolean, nullable=False, default=False)
    deice_spacing_threshold_minutes = db.Column(db.Integer, nullable=False, default=15)
    preferred_max_per_ramp = db.Column(db.Integer, nullable=True)
    inbound_same_ramp_spacing_minutes = db.Column(db.Integer, nullable=False, default=5)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gateway = db.relationship("Gateway")
