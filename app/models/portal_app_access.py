from datetime import datetime

from app.extensions import db


class PortalAppAccess(db.Model):
    __tablename__ = "portal_app_accesses"
    __table_args__ = (
        db.CheckConstraint(
            "app_code IN ('neogateway', 'neostaffing', 'neobid')",
            name="ck_portal_app_accesses_app_code",
        ),
        db.CheckConstraint(
            "status IN ('pending', 'approved', 'denied')",
            name="ck_portal_app_accesses_status",
        ),
        db.CheckConstraint(
            "role IN ('watcher', 'operator', 'simulator', 'master', 'grandmaster')",
            name="ck_portal_app_accesses_role",
        ),
        db.UniqueConstraint(
            "user_id",
            "app_code",
            name="uq_portal_app_accesses_user_app",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    app_code = db.Column(db.String(32), nullable=False, index=True)
    status = db.Column(db.String(16), nullable=False, default="pending")
    role = db.Column(db.String(32), nullable=False, default="watcher")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    approved_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    approval_notes = db.Column(db.Text, nullable=True)
    denied_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    denied_at = db.Column(db.DateTime, nullable=True)
    denial_notes = db.Column(db.Text, nullable=True)
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
        backref="portal_app_accesses",
    )
    approved_by_user = db.relationship(
        "User",
        foreign_keys=[approved_by_user_id],
        backref="approved_portal_app_accesses",
    )
    denied_by_user = db.relationship(
        "User",
        foreign_keys=[denied_by_user_id],
        backref="denied_portal_app_accesses",
    )
