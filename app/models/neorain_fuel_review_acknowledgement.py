from datetime import datetime

from app.extensions import db


class NeoRainFuelReviewAcknowledgement(db.Model):
    """One global acknowledgement of one published Scorpion fuel revision."""

    __tablename__ = "neorain_fuel_review_acknowledgements"
    __table_args__ = (
        db.UniqueConstraint(
            "sort_date_mission_id",
            "fuel_revision",
            name="uq_neorain_fuel_review_mission_revision",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(
        db.Integer, db.ForeignKey("sort_date_operations.id"), nullable=False, index=True
    )
    sort_date_mission_id = db.Column(
        db.Integer, db.ForeignKey("sort_date_missions.id"), nullable=False, index=True
    )
    fuel_revision = db.Column(db.String(96), nullable=False)
    reviewed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    reviewed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    operation = db.relationship("SortDateOperation")
    mission = db.relationship("SortDateMission")
    reviewed_by = db.relationship("User")
