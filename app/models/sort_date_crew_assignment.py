from datetime import datetime

from app.extensions import db


class SortDateCrewAssignment(db.Model):
    __tablename__ = "sort_date_crew_assignments"
    __table_args__ = (
        db.CheckConstraint(
            "aircraft_section IN ('topside', 'front_p', 'rear_p', 'ab', 'belly_31', "
            "'belly_34', 'other')",
            name="ck_sort_date_crew_assignments_aircraft_section",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_date_mission_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_missions.id"),
        nullable=False,
        index=True,
    )
    aircraft_section = db.Column(db.String(32), nullable=False, index=True)
    required = db.Column(db.Boolean, nullable=False, default=True)
    crew_id = db.Column(db.Integer, db.ForeignKey("crews.id"), nullable=True, index=True)
    assigned_at_utc = db.Column(db.DateTime, nullable=True)
    marked_not_required_at_utc = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    crew = db.relationship("Crew")
    sort_date_mission = db.relationship("SortDateMission")
