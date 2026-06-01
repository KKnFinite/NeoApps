from datetime import datetime

from app.extensions import db


class SortDateTailState(db.Model):
    __tablename__ = "sort_date_tail_states"
    __table_args__ = (
        db.CheckConstraint(
            "deice_status IN ('unknown', 'negative', 'required', 'configured', 'cleared')",
            name="ck_sort_date_tail_states_deice_status",
        ),
        db.CheckConstraint(
            "aircraft_type_source IN ('derived', 'manual', 'api', 'unknown')",
            name="ck_sort_date_tail_states_aircraft_type_source",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_date = db.Column(db.Date, nullable=False, index=True)
    gateway_code = db.Column(db.String(8), nullable=False, index=True)
    sort_name = db.Column(db.String(32), nullable=False, index=True)
    tail_number = db.Column(db.String(32), nullable=False, index=True)
    aircraft_type = db.Column(db.String(32), nullable=True)
    aircraft_type_source = db.Column(db.String(32), nullable=False, default="unknown")
    parking_position = db.Column(db.String(64), nullable=True)
    fuel_onboard = db.Column(db.Integer, nullable=True)
    mechanical_status = db.Column(db.Boolean, nullable=False, default=False)
    pushback_status = db.Column(db.Boolean, nullable=False, default=False)
    deice_status = db.Column(db.String(32), nullable=False, default="unknown")
    pretreat_status = db.Column(db.Boolean, nullable=False, default=False)
    deice_completed_at_utc = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    @property
    def deice_complete(self):
        return bool(self.pretreat_status or self.deice_status == "cleared")
