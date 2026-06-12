from datetime import datetime

from app.extensions import db


class NeoSektorWaveState(db.Model):
    __tablename__ = "neosektor_wave_states"
    __table_args__ = (
        db.UniqueConstraint(
            "sort_state_id",
            "wave_name",
            name="uq_neosektor_wave_states_sort_wave",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_state_id = db.Column(
        db.Integer,
        db.ForeignKey("neosektor_sort_states.id"),
        nullable=False,
        index=True,
    )
    wave_name = db.Column(db.String(32), nullable=False, index=True)
    planned_count = db.Column(db.Integer, nullable=False, default=0)
    unloaded_count = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(32), nullable=False, default="Empty")
    display_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    sort_state = db.relationship("NeoSektorSortState", backref="wave_states")
