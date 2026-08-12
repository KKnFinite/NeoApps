from datetime import datetime

from app.extensions import db


class NeoErmacDoorSupervision(db.Model):
    __tablename__ = "neoermac_door_supervisions"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "sort_date_operation_id",
            name="uq_neoermac_door_supervisions_user_operation",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sort_date_operation_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_operations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    selected_doors_json = db.Column(db.Text, nullable=False, default="[]")
    active_door = db.Column(db.String(8), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    user = db.relationship("User")
    sort_date_operation = db.relationship("SortDateOperation")
