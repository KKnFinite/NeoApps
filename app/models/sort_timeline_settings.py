from datetime import datetime

from app.extensions import db


class SortTimelineSettings(db.Model):
    __tablename__ = "sort_timeline_settings"
    __table_args__ = (
        db.UniqueConstraint("gateway_id", name="uq_sort_timeline_settings_gateway"),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=False, index=True)
    gateway_code = db.Column(db.String(8), nullable=False, index=True)
    monthly_api_units = db.Column("monthly_api_limit", db.Integer, nullable=False, default=600)
    units_per_poll = db.Column(db.Integer, nullable=False, default=2)
    taxi_to_ramp_minutes = db.Column(db.Integer, nullable=False, default=10)
    provider_enabled = db.Column(db.Boolean, nullable=False, default=False)
    provider_name = db.Column(db.String(120), nullable=False, default="")
    api_key_env_var_name = db.Column(db.String(120), nullable=False, default="")
    _legacy_operating_weekdays = db.Column("operating_weekdays", db.String(120), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gateway = db.relationship("Gateway")
    sort_settings = db.relationship(
        "SortTimelineSortSetting",
        back_populates="timeline_settings",
        cascade="all, delete-orphan",
    )


class SortTimelineMonthVariance(db.Model):
    __tablename__ = "sort_timeline_month_variances"
    __table_args__ = (
        db.UniqueConstraint(
            "gateway_id",
            "month_number",
            name="uq_sort_timeline_month_variance",
        ),
        db.CheckConstraint(
            "month_number >= 1 AND month_number <= 12",
            name="ck_sort_timeline_month_variance_month_number",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=False, index=True)
    gateway_code = db.Column(db.String(8), nullable=False, index=True)
    month_number = db.Column(db.Integer, nullable=False, index=True)
    variance = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gateway = db.relationship("Gateway")


class SortTimelineApiParticipation(db.Model):
    __tablename__ = "sort_timeline_api_participation"
    __table_args__ = (
        db.UniqueConstraint(
            "gateway_id",
            "day_of_week",
            "sort_name",
            name="uq_sort_timeline_api_participation",
        ),
        db.CheckConstraint(
            "day_of_week IN ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')",
            name="ck_sort_timeline_api_participation_day",
        ),
        db.CheckConstraint(
            "sort_name IN ('twilight', 'night', 'sunrise', 'day')",
            name="ck_sort_timeline_api_participation_sort",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=False, index=True)
    gateway_code = db.Column(db.String(8), nullable=False, index=True)
    day_of_week = db.Column(db.String(16), nullable=False, index=True)
    sort_name = db.Column(db.String(32), nullable=False, index=True)
    is_enabled = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gateway = db.relationship("Gateway")


class SortTimelineSortSetting(db.Model):
    __tablename__ = "sort_timeline_sort_settings"
    __table_args__ = (
        db.UniqueConstraint(
            "settings_id",
            "sort_name",
            name="uq_sort_timeline_sort_setting",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    settings_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_timeline_settings.id"),
        nullable=False,
        index=True,
    )
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=False, index=True)
    gateway_code = db.Column(db.String(8), nullable=False, index=True)
    sort_name = db.Column(db.String(32), nullable=False, index=True)
    sort_window_start_local = db.Column(db.Time, nullable=True)
    sort_window_end_local = db.Column(db.Time, nullable=True)
    ops_window_start_local = db.Column(db.Time, nullable=True)
    ops_window_end_local = db.Column(db.Time, nullable=True)
    polling_start_local = db.Column(db.Time, nullable=True)
    polling_end_local = db.Column(db.Time, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gateway = db.relationship("Gateway")
    timeline_settings = db.relationship("SortTimelineSettings", back_populates="sort_settings")
    special_poll_times = db.relationship(
        "SortTimelineSpecialPollTime",
        back_populates="sort_setting",
        cascade="all, delete-orphan",
        order_by="SortTimelineSpecialPollTime.poll_time_local",
    )


class SortTimelineSpecialPollTime(db.Model):
    __tablename__ = "sort_timeline_special_poll_times"
    __table_args__ = (
        db.UniqueConstraint(
            "sort_setting_id",
            "poll_time_local",
            name="uq_sort_timeline_special_poll_time",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_setting_id = db.Column(
        db.Integer,
        db.ForeignKey("sort_timeline_sort_settings.id"),
        nullable=False,
        index=True,
    )
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=False, index=True)
    gateway_code = db.Column(db.String(8), nullable=False, index=True)
    sort_name = db.Column(db.String(32), nullable=False, index=True)
    poll_time_local = db.Column(db.Time, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gateway = db.relationship("Gateway")
    sort_setting = db.relationship("SortTimelineSortSetting", back_populates="special_poll_times")


class SortTimelineUsageCounter(db.Model):
    __tablename__ = "sort_timeline_usage_counters"
    __table_args__ = (
        db.UniqueConstraint(
            "gateway_id",
            "month_key",
            name="uq_sort_timeline_usage_counter_gateway_month",
        ),
        db.CheckConstraint(
            "attempted_call_count >= 0",
            name="ck_sort_timeline_usage_attempts_nonnegative",
        ),
        db.CheckConstraint(
            "units_consumed >= 0",
            name="ck_sort_timeline_usage_units_nonnegative",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=False, index=True)
    gateway_code = db.Column(db.String(8), nullable=False, index=True)
    month_key = db.Column(db.String(7), nullable=False, index=True)
    attempted_call_count = db.Column(db.Integer, nullable=False, default=0)
    units_consumed = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gateway = db.relationship("Gateway")
