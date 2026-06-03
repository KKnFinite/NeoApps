from sqlalchemy import inspect, text

from app.extensions import db


LOCAL_SQLITE_GATEWAY_COLUMNS = {
    "sort_date_operations": "gateway_id",
    "master_flight_schedules": "gateway_id",
    "crews": "gateway_id",
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

    db.session.flush()
