from datetime import datetime

from app.extensions import db


class NeoSektorUldOnTheWayEvent(db.Model):
    __tablename__ = "neosektor_uld_on_the_way_events"
    __table_args__ = (
        db.CheckConstraint(
            "quantity > 0",
            name="ck_neosektor_uld_on_way_quantity_positive",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=False, index=True)
    door = db.Column(db.String(8), nullable=False, index=True)
    uld_type = db.Column(db.String(8), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False)
    sent_at_utc = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    expires_at_utc = db.Column(db.DateTime, nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    gateway = db.relationship("Gateway")
