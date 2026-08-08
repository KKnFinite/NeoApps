from datetime import datetime

from app.extensions import db


class SortDateGoogleMissionLink(db.Model):
    __tablename__ = "sort_date_google_mission_links"
    __table_args__ = (
        db.CheckConstraint(
            "mission_type IN ('arrival', 'departure')",
            name="ck_sort_date_google_mission_links_mission_type",
        ),
        db.UniqueConstraint(
            "sort_date_operation_id",
            "mission_type",
            "source_sheet",
            "source_row",
            name="uq_sort_date_google_mission_links_operation_direction_row",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_operations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sort_date_mission_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_missions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    mission_type = db.Column(db.String(16), nullable=False, index=True)
    source_sheet = db.Column(db.String(32), nullable=False, index=True)
    source_row = db.Column(db.Integer, nullable=False, index=True)
    last_flight_number = db.Column(db.String(32), nullable=True)
    last_tail_number = db.Column(db.String(32), nullable=True)
    last_status_raw = db.Column(db.String(32), nullable=True)
    google_eta_datetime_utc = db.Column(db.DateTime, nullable=True)
    pending_tail_number = db.Column(db.String(32), nullable=True)
    pending_swap_flight_number = db.Column(db.String(32), nullable=True)
    pending_swap_destination = db.Column(db.String(8), nullable=True)
    pending_swap_acknowledgment = db.Column(db.String(32), nullable=True)
    last_applied_at_utc = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    sort_date_operation = db.relationship(
        "SortDateOperation",
        back_populates="google_mission_links",
    )
    sort_date_mission = db.relationship("SortDateMission")
