from datetime import datetime

from app.extensions import db


class GatewayMembership(db.Model):
    __tablename__ = "gateway_memberships"
    __table_args__ = (
        db.CheckConstraint(
            "status IN ('pending', 'approved', 'denied')",
            name="ck_gateway_memberships_status",
        ),
        db.UniqueConstraint(
            "user_id",
            "gateway_id",
            name="uq_gateway_memberships_user_gateway",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    gateway_id = db.Column(
        db.Integer,
        db.ForeignKey("gateways.id"),
        nullable=False,
        index=True,
    )
    status = db.Column(db.String(16), nullable=False, default="pending")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    user = db.relationship("User", backref="gateway_memberships")
    gateway = db.relationship("Gateway", back_populates="memberships")
    node_roles = db.relationship(
        "GatewayNodeRole",
        back_populates="gateway_membership",
        cascade="all, delete-orphan",
    )
