from datetime import date, datetime

from sqlalchemy import text

from app.extensions import db


class StaffingReportingRelationship(db.Model):
    __tablename__ = "staffing_reporting_relationships"
    __table_args__ = (
        db.CheckConstraint(
            "person_id <> reports_to_person_id",
            name="ck_staffing_reporting_relationships_not_self",
        ),
        db.CheckConstraint(
            "effective_end IS NULL OR effective_end >= effective_start",
            name="ck_staffing_reporting_relationships_dates",
        ),
        db.Index(
            "uq_staffing_reporting_relationships_active_person",
            "person_id",
            unique=True,
            postgresql_where=text("active"),
            sqlite_where=text("active = 1"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_people.id"),
        nullable=False,
        index=True,
    )
    reports_to_person_id = db.Column(
        db.Integer,
        db.ForeignKey("staffing_people.id"),
        nullable=False,
        index=True,
    )
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    effective_start = db.Column(db.Date, nullable=False, default=date.today)
    effective_end = db.Column(db.Date, nullable=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    person = db.relationship(
        "StaffingPerson",
        foreign_keys=[person_id],
        back_populates="reporting_relationships",
    )
    reports_to_person = db.relationship(
        "StaffingPerson",
        foreign_keys=[reports_to_person_id],
        back_populates="direct_report_relationships",
    )
