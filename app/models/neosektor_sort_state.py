from datetime import datetime

from app.extensions import db


class NeoSektorSortState(db.Model):
    __tablename__ = "neosektor_sort_states"
    __table_args__ = (
        db.UniqueConstraint(
            "gateway_id",
            "sort_date",
            "sort_name",
            name="uq_neosektor_sort_states_gateway_date_sort",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=False, index=True)
    gateway_code = db.Column(db.String(8), nullable=False, index=True)
    sort_date = db.Column(db.Date, nullable=False, index=True)
    sort_name = db.Column(db.String(32), nullable=False, default="night", index=True)
    active_wave = db.Column(db.String(32), nullable=False, default="1ST WAVE")
    planned_total = db.Column(db.Integer, nullable=False, default=0)
    unloaded_total = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gateway = db.relationship("Gateway")
