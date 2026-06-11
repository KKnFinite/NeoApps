from datetime import datetime

from app.extensions import db


class NeoErmacUldRequest(db.Model):
    __tablename__ = "neoermac_uld_requests"
    __table_args__ = (
        db.UniqueConstraint(
            "gateway_id",
            "door",
            name="uq_neoermac_uld_requests_gateway_door",
        ),
        db.CheckConstraint("a2_count >= 0", name="ck_neoermac_uld_requests_a2_nonnegative"),
        db.CheckConstraint("a1_count >= 0", name="ck_neoermac_uld_requests_a1_nonnegative"),
        db.CheckConstraint("amp_count >= 0", name="ck_neoermac_uld_requests_amp_nonnegative"),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=False, index=True)
    door = db.Column(db.String(8), nullable=False, index=True)
    a2_count = db.Column(db.Integer, nullable=False, default=0)
    a1_count = db.Column(db.Integer, nullable=False, default=0)
    amp_count = db.Column(db.Integer, nullable=False, default=0)
    setup_needed = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gateway = db.relationship("Gateway")
