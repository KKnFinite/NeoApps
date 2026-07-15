from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db


ROLE_LEVELS = {
    "watcher": 10,
    "operator": 20,
    "simulator": 30,
    "master": 40,
    "grandmaster": 50,
}

MANAGEMENT_LEVELS = (
    "part_time_supervisor",
    "full_time_supervisor",
    "full_time_specialist",
    "manager",
    "division_manager",
)


class User(UserMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (
        db.CheckConstraint(
            "role IN ('grandmaster', 'master', 'simulator', 'operator', 'watcher')",
            name="ck_users_role_supported",
        ),
        db.CheckConstraint(
            "management_level IS NULL OR management_level IN ("
            "'part_time_supervisor', 'full_time_supervisor', "
            "'full_time_specialist', 'manager', 'division_manager')",
            name="ck_users_management_level_supported",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=True, index=True)
    first_name = db.Column(db.String(80), nullable=True)
    last_name = db.Column(db.String(80), nullable=True)
    full_name = db.Column(db.String(160), nullable=True)
    employee_id = db.Column(db.String(80), unique=True, nullable=True, index=True)
    supervisor_name = db.Column(db.String(160), nullable=True)
    work_area = db.Column(db.String(160), nullable=True)
    is_management = db.Column(db.Boolean, nullable=False, default=False, index=True)
    management_level = db.Column(db.String(40), nullable=True, index=True)
    access_reason = db.Column(db.Text, nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(32), nullable=False, default="watcher")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    email_verified_at = db.Column(db.DateTime, nullable=True)
    password_reset_required = db.Column(db.Boolean, nullable=False, default=False)
    password_policy_update_required = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )
    password_changed_at = db.Column(db.DateTime, nullable=True)
    last_password_reset_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )
    last_password_reset_at = db.Column(db.DateTime, nullable=True)
    last_password_reset_reason = db.Column(db.Text, nullable=True)
    mfa_required = db.Column(db.Boolean, nullable=False, default=False)
    mfa_enabled = db.Column(db.Boolean, nullable=False, default=False)
    mfa_secret = db.Column(db.String(255), nullable=True)
    mfa_verified_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)

    last_password_reset_by_user = db.relationship(
        "User",
        remote_side=[id],
        foreign_keys=[last_password_reset_by_user_id],
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def display_name(self):
        name = " ".join(
            part for part in (self.first_name, self.last_name) if part
        ).strip()
        return name or self.full_name or self.email or self.username

    @property
    def header_display_name(self):
        last_name = (self.last_name or "").strip()
        return last_name or self.display_name

    @property
    def role_level(self):
        return ROLE_LEVELS.get(self.role, 0)

    def can_manage_users(self):
        return self.role in {"grandmaster", "master"}

    def can_manage_role(self, target_role):
        if not self.can_manage_users():
            return False

        target_level = ROLE_LEVELS.get(target_role)
        if target_level is None:
            return False

        if self.role == "grandmaster":
            return target_level <= ROLE_LEVELS["grandmaster"]

        return target_level < ROLE_LEVELS["master"]

    def can_promote_to(self, target_role):
        return self.can_manage_role(target_role)
