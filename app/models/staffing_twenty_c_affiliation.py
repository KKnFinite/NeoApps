from datetime import date, datetime

from sqlalchemy import text

from app.extensions import db


TWENTY_C_AFFILIATION_TYPES = ("primary", "secondary")


class StaffingTwentyCAffiliation(db.Model):
    """A durable by-Sort operational affiliation between a 20C and an FT Supervisor."""

    __tablename__ = "staffing_twenty_c_affiliations"
    __table_args__ = (
        db.CheckConstraint(
            "affiliation_type IN ('primary', 'secondary')",
            name="ck_staffing_twenty_c_affiliations_type",
        ),
        db.CheckConstraint(
            "twenty_c_person_id <> ft_supervisor_person_id",
            name="ck_staffing_twenty_c_affiliations_not_self",
        ),
        db.CheckConstraint(
            "effective_end IS NULL OR effective_end >= effective_start",
            name="ck_staffing_twenty_c_affiliations_dates",
        ),
        db.Index(
            "uq_staffing_twenty_c_affiliations_active_target",
            "twenty_c_person_id",
            "ft_supervisor_person_id",
            "sort_unit_id",
            unique=True,
            postgresql_where=text("active"),
            sqlite_where=text("active = 1"),
        ),
        db.Index(
            "uq_staffing_twenty_c_affiliations_active_primary",
            "twenty_c_person_id",
            "sort_unit_id",
            unique=True,
            postgresql_where=text("active AND affiliation_type = 'primary'"),
            sqlite_where=text("active = 1 AND affiliation_type = 'primary'"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    twenty_c_person_id = db.Column(
        db.Integer, db.ForeignKey("staffing_people.id"), nullable=False, index=True
    )
    ft_supervisor_person_id = db.Column(
        db.Integer, db.ForeignKey("staffing_people.id"), nullable=False, index=True
    )
    sort_unit_id = db.Column(
        db.Integer, db.ForeignKey("staffing_units.id"), nullable=False, index=True
    )
    affiliation_type = db.Column(db.String(16), nullable=False, index=True)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    effective_start = db.Column(db.Date, nullable=False, default=date.today)
    effective_end = db.Column(db.Date, nullable=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    twenty_c_person = db.relationship(
        "StaffingPerson",
        foreign_keys=[twenty_c_person_id],
        back_populates="twenty_c_affiliations",
    )
    ft_supervisor_person = db.relationship(
        "StaffingPerson",
        foreign_keys=[ft_supervisor_person_id],
        back_populates="twenty_c_supervisor_affiliations",
    )
    sort_unit = db.relationship("StaffingUnit", foreign_keys=[sort_unit_id])
