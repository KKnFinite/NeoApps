from datetime import datetime

from app.extensions import db


class NeoSektorBayStatus(db.Model):
    __tablename__ = "neosektor_bay_statuses"
    __table_args__ = (
        db.UniqueConstraint(
            "sort_state_id",
            "bay_name",
            name="uq_neosektor_bay_statuses_sort_bay",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_state_id = db.Column(
        db.Integer,
        db.ForeignKey("neosektor_sort_states.id"),
        nullable=False,
        index=True,
    )
    bay_name = db.Column(db.String(32), nullable=False, index=True)
    side = db.Column(db.String(16), nullable=False, default="")
    status = db.Column(db.String(32), nullable=False, default="Empty")
    display_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    sort_state = db.relationship("NeoSektorSortState", backref="bay_statuses")
