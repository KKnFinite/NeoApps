from datetime import datetime

from app.extensions import db


class NeoSubZeroDepartureDeiceEvent(db.Model):
    __tablename__ = "neosubzero_departure_deice_events"
    __table_args__ = (
        db.UniqueConstraint(
            "sort_date_mission_id",
            name="uq_neosubzero_departure_deice_mission",
        ),
        db.CheckConstraint(
            "status IN ('deice_planned', 'configured', 'finished', 'cleared', "
            "'negative', 'not_sprayed')",
            name="ck_neosubzero_departure_deice_status",
        ),
        db.CheckConstraint(
            "treatment_plan IS NULL OR treatment_plan IN "
            "('one_type_i', 'two_type_i', 'type_i_type_iv')",
            name="ck_neosubzero_departure_deice_plan",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_operations.id"),
        nullable=False,
        index=True,
    )
    sort_date_mission_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_missions.id"),
        nullable=False,
        index=True,
    )
    tail_number = db.Column(db.String(32), nullable=False, index=True)
    status = db.Column(db.String(24), nullable=False, default="deice_planned")
    configured_at_utc = db.Column(db.DateTime, nullable=True)
    treatment_plan = db.Column(db.String(24), nullable=True)
    pass1_surface_area = db.Column(db.String(32), nullable=True)
    pass1_started_at_utc = db.Column(db.DateTime, nullable=True)
    pass1_ended_at_utc = db.Column(db.DateTime, nullable=True)
    pass2_surface_area = db.Column(db.String(32), nullable=True)
    pass2_started_at_utc = db.Column(db.DateTime, nullable=True)
    pass2_ended_at_utc = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    operation = db.relationship("SortDateOperation")
    mission = db.relationship("SortDateMission")
