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
    approved_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    approval_notes = db.Column(db.Text, nullable=True)
    denied_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    denied_at = db.Column(db.DateTime, nullable=True)
    denial_notes = db.Column(db.Text, nullable=True)
    approval_email_sent_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    user = db.relationship(
        "User",
        foreign_keys=[user_id],
        backref="gateway_memberships",
    )
    approved_by_user = db.relationship(
        "User",
        foreign_keys=[approved_by_user_id],
        backref="approved_gateway_memberships",
    )
    denied_by_user = db.relationship(
        "User",
        foreign_keys=[denied_by_user_id],
        backref="denied_gateway_memberships",
    )
    gateway = db.relationship("Gateway", back_populates="memberships")
    node_roles = db.relationship(
        "GatewayNodeRole",
        back_populates="gateway_membership",
        cascade="all, delete-orphan",
    )
