from datetime import datetime

from app.extensions import db
from app.models.user import ROLE_LEVELS


class GatewayNodeRole(db.Model):
    __tablename__ = "gateway_node_roles"
    __table_args__ = (
        db.CheckConstraint(
            "role IN ('watcher', 'operator', 'simulator', 'master', 'grandmaster')",
            name="ck_gateway_node_roles_role",
        ),
        db.UniqueConstraint(
            "gateway_membership_id",
            "node_id",
            name="uq_gateway_node_roles_membership_node",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_membership_id = db.Column(
        db.Integer,
        db.ForeignKey("gateway_memberships.id"),
        nullable=False,
        index=True,
    )
    node_id = db.Column(db.Integer, db.ForeignKey("neo_nodes.id"), nullable=False, index=True)
    role = db.Column(db.String(32), nullable=False, default="watcher")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gateway_membership = db.relationship(
        "GatewayMembership",
        back_populates="node_roles",
    )
    node = db.relationship("NeoNode", back_populates="gateway_node_roles")

    @property
    def role_level(self):
        return ROLE_LEVELS.get(self.role, 0)
