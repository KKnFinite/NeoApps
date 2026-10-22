"""Private 24-hour recovery payloads; tombstones contain no employee profile."""
from uuid import uuid4
from sqlalchemy import event
from app.extensions import db


class StaffingEmployeeRecovery(db.Model):
    __tablename__ = 'staffing_employee_recoveries'
    __table_args__ = (
        db.CheckConstraint("state IN ('preparing','deleted','restored','purged')", name='ck_employee_recovery_state'),
    )
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid4()))
    # Intentionally not an FK: the original identity must survive deletion.
    person_id = db.Column(db.Integer, nullable=False, index=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    state = db.Column(db.String(16), nullable=False)
    transaction_id = db.Column(db.BigInteger)
    encrypted_payload = db.Column(db.LargeBinary)
    restored_at = db.Column(db.DateTime)
    restored_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    purged_at = db.Column(db.DateTime)


class StaffingEmployeeRecoveryAudit(db.Model):
    __tablename__ = 'staffing_employee_recovery_audits'
    id = db.Column(db.Integer, primary_key=True)
    recovery_id = db.Column(db.String(36), db.ForeignKey('staffing_employee_recoveries.id'), nullable=False, index=True)
    action = db.Column(db.String(20), nullable=False)
    actor_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    occurred_at = db.Column(db.DateTime, nullable=False)
    row_counts = db.Column(db.JSON, nullable=False)


@event.listens_for(db.metadata, 'after_create')
def recovery_guards_after_create(_metadata, connection, **_kwargs):
    from app.services.neostaffing_employee_recovery_schema import install_recovery_guards
    install_recovery_guards(connection)
