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
}


def sync_local_sqlite_schema(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", ""))
    if not database_uri.startswith("sqlite:"):
        return

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
