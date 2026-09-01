from datetime import datetime

from app.extensions import db


class NeoRainLoadPlannerContact(db.Model):
    """Gateway-scoped operational contact details for one Load Planner."""

    __tablename__ = "neorain_load_planner_contacts"
    __table_args__ = (
        db.UniqueConstraint(
            "gateway_id",
            "staffing_person_id",
            name="uq_neorain_load_planner_contact_gateway_person",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(
        db.Integer,
        db.ForeignKey("gateways.id"),
        nullable=False,
        index=True,
    )
    staffing_person_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_people.id"),
        nullable=False,
        index=True,
    )
    extension = db.Column(db.String(64), nullable=True)
    radio_channel = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gateway = db.relationship("Gateway")
    staffing_person = db.relationship("StaffingPerson")
