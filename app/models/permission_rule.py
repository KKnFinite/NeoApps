from datetime import datetime

from app.extensions import db


class PermissionRule(db.Model):
    __tablename__ = "permission_rules"
    __table_args__ = (
        db.CheckConstraint(
            "minimum_role IN ('watcher', 'operator', 'simulator', 'master', 'grandmaster')",
            name="ck_permission_rules_minimum_role",
        ),
        db.UniqueConstraint("permission_key", name="uq_permission_rules_key"),
    )

    id = db.Column(db.Integer, primary_key=True)
    permission_key = db.Column(db.String(160), nullable=False, unique=True, index=True)
    minimum_role = db.Column(db.String(32), nullable=False, default="watcher")
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
