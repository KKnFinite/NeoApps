from datetime import datetime

from app.extensions import db


class NeoRainGoogleRolloverState(db.Model):
    """Persistent per-sort baseline fence for one legacy Rain sheet row."""

    __tablename__ = "neorain_google_rollover_states"
    __table_args__ = (
        db.UniqueConstraint(
            "sort_date_operation_id",
            "sheet_row",
            name="uq_neorain_google_rollover_operation_row",
        ),
        db.CheckConstraint(
            "sheet_row > 0",
            name="ck_neorain_google_rollover_sheet_row_positive",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_date_operations.id"),
        nullable=False,
        index=True,
    )
    sheet_row = db.Column(db.Integer, nullable=False)
    baseline_values_json = db.Column(db.Text, nullable=False, default="{}")
    released_fields_json = db.Column(db.Text, nullable=False, default="[]")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    operation = db.relationship("SortDateOperation")
