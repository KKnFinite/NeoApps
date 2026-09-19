"""By-exception personnel documentation; never a discipline calculation."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import event, DDL
from app.extensions import db


class StaffingEmployeeRecordSetting(db.Model):
    __tablename__ = "staffing_employee_record_settings"
    unit_id = db.Column(db.Integer, db.ForeignKey("staffing_units.id"), primary_key=True)
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    version = db.Column(db.Integer, nullable=False, default=0)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class StaffingEmployeeRecord(db.Model):
    __tablename__ = "staffing_employee_records"
    __table_args__ = (
        db.CheckConstraint("kind IN ('talk_with','verbal','written_warning')", name="ck_employee_record_kind"),
        db.CheckConstraint("(finalized_at IS NULL AND acknowledgment IS NULL AND signature_key IS NULL) OR "
                           "(finalized_at IS NOT NULL AND finalized_by IS NOT NULL AND "
                           "((acknowledgment IN ('rts','delivered') AND signature_key IS NULL) OR "
                           "(acknowledgment = 'signature' AND signature_key IS NOT NULL AND signature_sha256 IS NOT NULL)))",
                           name="ck_employee_record_finalization"),
        db.Index("ix_employee_record_history", "person_id", "created_at", "id"),
    )
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid4()))
    person_id = db.Column(db.Integer, db.ForeignKey("staffing_people.id", ondelete="RESTRICT"), nullable=False)
    kind = db.Column(db.String(24), nullable=False)
    body = db.Column(db.Text, nullable=False)
    context_json = db.Column(db.Text, nullable=False)
    # Stable source identity + snapshot survive the discipline retention window.
    # Intentionally not a cascading FK to expiring resolution facts.
    discipline_resolution_id = db.Column(db.Integer)
    discipline_snapshot_json = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    version = db.Column(db.Integer, nullable=False, default=1)
    finalized_at = db.Column(db.DateTime)
    finalized_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    acknowledgment = db.Column(db.String(16))
    acknowledgment_text = db.Column(db.String(100))
    signature_key = db.Column(db.String(255))
    signature_sha256 = db.Column(db.String(64))
    signature_size = db.Column(db.Integer)


class StaffingEmployeeRecordEvent(db.Model):
    """Append-only draft revisions, finalization and dated addenda."""
    __tablename__ = "staffing_employee_record_events"
    __table_args__ = (db.UniqueConstraint("record_id", "sequence", name="uq_employee_record_event_sequence"),)
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid4()))
    record_id = db.Column(db.String(36), db.ForeignKey("staffing_employee_records.id", ondelete="RESTRICT"), nullable=False, index=True)
    sequence = db.Column(db.Integer, nullable=False)
    kind = db.Column(db.String(20), nullable=False)
    body = db.Column(db.Text, nullable=False)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


for table, condition in ((StaffingEmployeeRecord.__table__, "OLD.finalized_at IS NOT NULL"),
                         (StaffingEmployeeRecordEvent.__table__, "TRUE")):
    name = table.name
    event.listen(table, "after_create", DDL(f"""
        CREATE OR REPLACE FUNCTION {name}_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF {condition} THEN RAISE EXCEPTION 'Employee Record history is immutable'; END IF;
          IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER {name}_immutable BEFORE UPDATE OR DELETE ON {name}
        FOR EACH ROW EXECUTE FUNCTION {name}_immutable();
    """).execute_if(dialect="postgresql"))
    for operation in ("UPDATE", "DELETE"):
        event.listen(table, "after_create", DDL(f"""
            CREATE TRIGGER {name}_immutable_{operation.lower()} BEFORE {operation} ON {name}
            WHEN {condition} BEGIN SELECT RAISE(ABORT, 'Employee Record history is immutable'); END;
        """).execute_if(dialect="sqlite"))
