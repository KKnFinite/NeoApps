from datetime import datetime

from app.extensions import db


class SortDateAlpPreview(db.Model):
    __tablename__ = "sort_date_alp_previews"
    __table_args__ = (
        db.UniqueConstraint(
            "sort_date_operation_id",
            "mission_type",
            name="uq_sort_date_alp_preview_operation_type",
        ),
        db.CheckConstraint(
            "mission_type IN ('arrival', 'departure')",
            name="ck_sort_date_alp_previews_mission_type",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_operations.id"),
        nullable=False,
        index=True,
    )
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=True, index=True)
    gateway_code = db.Column(db.String(8), nullable=False, index=True)
    mission_type = db.Column(db.String(16), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    paste_text = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    sort_date_operation = db.relationship("SortDateOperation")
    user = db.relationship("User")
