from datetime import datetime

from app.extensions import db


class NeoSektorDriverRouteSetting(db.Model):
    __tablename__ = "neosektor_driver_route_settings"
    __table_args__ = (
        db.UniqueConstraint(
            "sort_state_id",
            "route_name",
            name="uq_neosektor_driver_route_settings_sort_route",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_state_id = db.Column(
        db.Integer,
        db.ForeignKey("neosektor_sort_states.id"),
        nullable=False,
        index=True,
    )
    route_name = db.Column(db.String(64), nullable=False, index=True)
    route_value = db.Column(db.String(64), nullable=False, default="")
    display_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    sort_state = db.relationship("NeoSektorSortState", backref="driver_route_settings")
