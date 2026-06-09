from datetime import datetime

from app.extensions import db


class NeoErmacBuildingLineup(db.Model):
    __tablename__ = "neoermac_building_lineups"
    __table_args__ = (
        db.UniqueConstraint(
            "gateway_id",
            "runout_key",
            name="uq_neoermac_building_lineups_gateway_runout",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=False, index=True)
    runout_key = db.Column(db.String(32), nullable=False)
    runout_name = db.Column(db.String(40), nullable=False)
    east_destination_1 = db.Column(db.String(8), nullable=True)
    east_destination_2 = db.Column(db.String(8), nullable=True)
    west_destination_1 = db.Column(db.String(8), nullable=True)
    west_destination_2 = db.Column(db.String(8), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gateway = db.relationship("Gateway")
