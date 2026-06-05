from datetime import datetime

from app.extensions import db


class GatewaySortMatrix(db.Model):
    __tablename__ = "gateway_sort_matrix"
    __table_args__ = (
        db.UniqueConstraint(
            "gateway_id",
            "day_of_week",
            "sort_name",
            name="uq_gateway_sort_matrix_gateway_day_sort",
        ),
        db.CheckConstraint(
            "day_of_week IN ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')",
            name="ck_gateway_sort_matrix_day_of_week",
        ),
        db.CheckConstraint(
            "sort_name IN ('twilight', 'night', 'sunrise', 'day')",
            name="ck_gateway_sort_matrix_sort_name",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=False, index=True)
    gateway_code = db.Column(db.String(8), nullable=False, index=True)
    day_of_week = db.Column(db.String(16), nullable=False, index=True)
    sort_name = db.Column(db.String(32), nullable=False, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gateway = db.relationship("Gateway")
