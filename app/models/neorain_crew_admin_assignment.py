from datetime import datetime

from app.extensions import db


class NeoRainCrewAdminAssignment(db.Model):
    __tablename__ = "neorain_crew_admin_assignments"
    __table_args__ = (db.UniqueConstraint("sort_date_operation_id", "person_id", name="uq_neorain_crew_admin_operation_person"),)

    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(db.Integer, db.ForeignKey("sort_date_operations.id"), nullable=False, index=True)
    person_id = db.Column(db.Integer, db.ForeignKey("staffing_people.id"), nullable=False, index=True)
    ramps_json = db.Column(db.Text, nullable=False, default="[]")
    printer_number = db.Column(db.String(64), nullable=True)
    van_number = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    operation = db.relationship("SortDateOperation")
    person = db.relationship("StaffingPerson")
