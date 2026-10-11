from datetime import datetime

from app.extensions import db


class NeoRainReportEntry(db.Model):
    """Sort-scoped operator fact or frozen Google fallback; never a Neo authority."""

    __tablename__ = "neorain_report_entries"
    __table_args__ = (
        db.UniqueConstraint("sort_date_operation_id", "field_name", "data_source", name="uq_neorain_report_entry_source"),
        db.CheckConstraint("data_source IN ('manual', 'google')", name="ck_neorain_report_entry_source"),
        db.Index("ix_neorain_report_entries_gateway_date", "gateway_id", "sort_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=False)
    sort_date_operation_id = db.Column(db.Integer, db.ForeignKey("sort_date_operations.id"), nullable=False)
    sort_date = db.Column(db.Date, nullable=False)
    sort_name = db.Column(db.String(32), nullable=False)
    field_name = db.Column(db.String(64), nullable=False)
    value = db.Column(db.Text, nullable=False)
    data_source = db.Column(db.String(16), nullable=False)
    source_report_date = db.Column(db.Date, nullable=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    operation = db.relationship("SortDateOperation")
