from sqlalchemy import inspect, text

from app.extensions import db


LOCAL_SQLITE_GATEWAY_COLUMNS = {
    "sort_date_operations": "gateway_id",
    "master_flight_schedules": "gateway_id",
    "crews": "gateway_id",
}

LOCAL_SQLITE_OPTIONAL_COLUMNS = {
    "users": {
        "email": "VARCHAR(255)",
        "first_name": "VARCHAR(80)",
        "last_name": "VARCHAR(80)",
        "full_name": "VARCHAR(160)",
        "employee_id": "VARCHAR(80)",
        "supervisor_name": "VARCHAR(160)",
        "work_area": "VARCHAR(160)",
        "access_reason": "TEXT",
        "email_verified_at": "DATETIME",
        "password_reset_required": "BOOLEAN DEFAULT 0",
        "password_changed_at": "DATETIME",
        "last_password_reset_by_user_id": "INTEGER",
        "last_password_reset_at": "DATETIME",
        "last_password_reset_reason": "TEXT",
    },
    "gateway_memberships": {
        "approved_by_user_id": "INTEGER",
        "approved_at": "DATETIME",
        "approval_notes": "TEXT",
        "denied_by_user_id": "INTEGER",
        "denied_at": "DATETIME",
        "denial_notes": "TEXT",
        "approval_email_sent_at": "DATETIME",
    },
    "sort_date_missions": {
        "arrival_status": "VARCHAR(32)",
        "wave": "VARCHAR(16)",
        "actual_pure_pull_time_local": "TIME",
        "actual_first_mix_pull_time_local": "TIME",
        "actual_second_mix_pull_time_local": "TIME",
    },
    "sort_date_operations": {
        "first_wave_window_minutes": "INTEGER",
        "second_wave_window_minutes": "INTEGER",
    },
    "master_flight_schedules": {
        "aircraft_type": "VARCHAR(16)",
        "wave": "VARCHAR(16)",
    },
    "sort_timeline_settings": {
        "units_per_poll": "INTEGER DEFAULT 2",
    },
}

POSTGRES_OPTIONAL_COLUMNS = {
    "users": {
        "first_name": "VARCHAR(80)",
        "last_name": "VARCHAR(80)",
    },
    "sort_date_missions": {
        "arrival_status": "VARCHAR(32)",
        "wave": "VARCHAR(16)",
        "actual_pure_pull_time_local": "TIME",
        "actual_first_mix_pull_time_local": "TIME",
        "actual_second_mix_pull_time_local": "TIME",
    },
    "sort_date_operations": {
        "first_wave_window_minutes": "INTEGER",
        "second_wave_window_minutes": "INTEGER",
    },
    "master_flight_schedules": {
        "aircraft_type": "VARCHAR(16)",
        "wave": "VARCHAR(16)",
    },
    "sort_timeline_settings": {
        "units_per_poll": "INTEGER DEFAULT 2",
    },
}


def sync_local_sqlite_schema(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", ""))
    if not database_uri.startswith("sqlite:"):
        return

    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())
    _create_missing_application_tables(table_names)
    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())

    for table_name, column_name in LOCAL_SQLITE_GATEWAY_COLUMNS.items():
        if table_name not in table_names:
            continue

        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        if column_name in existing_columns:
            continue

        db.session.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} INTEGER"))

    for table_name, columns in LOCAL_SQLITE_OPTIONAL_COLUMNS.items():
        if table_name not in table_names:
            continue

        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name, column_type in columns.items():
            if column_name in existing_columns:
                continue

            db.session.execute(
                text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
            )

    db.session.flush()


def sync_database_schema(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", ""))
    if database_uri.startswith("sqlite:"):
        sync_local_sqlite_schema(app)
        return

    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())
    _create_missing_application_tables(table_names)
    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())

    for table_name, columns in POSTGRES_OPTIONAL_COLUMNS.items():
        if table_name not in table_names:
            continue

        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name, column_type in columns.items():
            if column_name in existing_columns:
                continue

            db.session.execute(
                text(
                    f"ALTER TABLE {table_name} "
                    f"ADD COLUMN IF NOT EXISTS {column_name} {column_type}"
                )
            )

    db.session.flush()


def _create_missing_application_tables(existing_table_names):
    from app.models import (
        SortTimelineApiParticipation,
        SortTimelineMonthVariance,
        SortTimelineSettings,
        SortTimelineSortSetting,
        SortTimelineSpecialPollTime,
        SortTimelineUsageCounter,
    )

    for model in (
        SortTimelineSettings,
        SortTimelineApiParticipation,
        SortTimelineMonthVariance,
        SortTimelineSortSetting,
        SortTimelineSpecialPollTime,
        SortTimelineUsageCounter,
    ):
        if model.__tablename__ in existing_table_names:
            continue
        model.__table__.create(bind=db.engine, checkfirst=True)
