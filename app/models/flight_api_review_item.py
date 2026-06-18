from datetime import datetime

from app.extensions import db


class FlightApiReviewItem(db.Model):
    __tablename__ = "flight_api_review_items"
    __table_args__ = (
        db.UniqueConstraint(
            "sort_date_operation_id",
            "review_key",
            name="uq_flight_api_review_item_operation_key",
        ),
        db.CheckConstraint(
            "mission_type IN ('arrival', 'departure')",
            name="ck_flight_api_review_items_mission_type",
        ),
        db.CheckConstraint(
            "review_status IN ('pending', 'ignored', 'accepted')",
            name="ck_flight_api_review_items_status",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_operations.id"),
        nullable=False,
        index=True,
    )
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=True, index=True)
    gateway_code = db.Column(db.String(8), nullable=False, index=True)
    sort_date = db.Column(db.Date, nullable=False, index=True)
    sort_name = db.Column(db.String(32), nullable=False, index=True)
    mission_type = db.Column(db.String(16), nullable=False, index=True)
    review_key = db.Column(db.String(160), nullable=False, index=True)
    review_status = db.Column(db.String(16), nullable=False, default="pending", index=True)
    flight_number = db.Column(db.String(32), nullable=False)
    call_sign = db.Column(db.String(32), nullable=True)
    origin = db.Column(db.String(8), nullable=True)
    destination = db.Column(db.String(8), nullable=True)
    revised_time_utc = db.Column(db.DateTime, nullable=True)
    runway_time_utc = db.Column(db.DateTime, nullable=True)
    tail_number = db.Column(db.String(32), nullable=True)
    aircraft_model = db.Column(db.String(120), nullable=True)
    api_status = db.Column(db.String(64), nullable=True)
    raw_payload = db.Column(db.Text, nullable=True)
    accepted_mission_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_missions.id"),
        nullable=True,
        index=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    sort_date_operation = db.relationship("SortDateOperation")
    accepted_mission = db.relationship("SortDateMission")
