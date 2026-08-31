from datetime import datetime

from app.extensions import db


class StaffingPersonQualification(db.Model):
    """Reusable qualification state attached to the canonical Staffing person."""

    __tablename__ = "staffing_person_qualifications"
    __table_args__ = (
        db.UniqueConstraint(
            "person_id",
            "qualification_key",
            name="uq_staffing_person_qualification_key",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_people.id"),
        nullable=False,
        index=True,
    )
    qualification_key = db.Column(db.String(64), nullable=False, index=True)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    granted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    granted_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )
    revoked_at = db.Column(db.DateTime, nullable=True)
    revoked_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    person = db.relationship("StaffingPerson", back_populates="qualifications")
    granted_by_user = db.relationship("User", foreign_keys=[granted_by_user_id])
    revoked_by_user = db.relationship("User", foreign_keys=[revoked_by_user_id])
