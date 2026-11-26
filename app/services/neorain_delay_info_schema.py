"""Relax Delay Info fields and retain historical notes in their original rows."""
from sqlalchemy import inspect


def sync_neorain_delay_info_schema(connection):
    inspector = inspect(connection)
    if inspector.has_table("neorain_mission_notes"):
        existing = {column["name"] for column in inspector.get_columns("neorain_mission_notes")}
        for field, column_type in (("minutes", "INTEGER"), ("code", "VARCHAR(2)")):
            if field not in existing:
                connection.exec_driver_sql(f"ALTER TABLE neorain_mission_notes ADD COLUMN {field} {column_type}")
    if not inspector.has_table("neorain_delay_info"):
        return
    required = {column["name"] for column in inspector.get_columns("neorain_delay_info")
                if not column["nullable"]}
    if not required.intersection(("minutes", "code")):
        return
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql(
            "ALTER TABLE neorain_delay_info ALTER COLUMN minutes DROP NOT NULL, ALTER COLUMN code DROP NOT NULL"
        )
    elif connection.dialect.name == "sqlite":
        # SQLite cannot drop a NOT NULL constraint in place. Swap only after
        # the original rows are copied, with the same primary key and indexes.
        with connection.begin_nested():
            connection.exec_driver_sql("""
                CREATE TABLE neorain_delay_info_nullable (
                    id INTEGER NOT NULL PRIMARY KEY,
                    sort_date_mission_id INTEGER NOT NULL REFERENCES sort_date_missions(id),
                    minutes INTEGER, code VARCHAR(2), notes TEXT,
                    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
                )
            """)
            connection.exec_driver_sql("""
                INSERT INTO neorain_delay_info_nullable
                    (id, sort_date_mission_id, minutes, code, notes, created_at, updated_at)
                SELECT id, sort_date_mission_id, minutes, code, notes, created_at, updated_at
                FROM neorain_delay_info
            """)
            connection.exec_driver_sql("DROP TABLE neorain_delay_info")
            connection.exec_driver_sql("ALTER TABLE neorain_delay_info_nullable RENAME TO neorain_delay_info")
            for field in ("sort_date_mission_id", "code"):
                connection.exec_driver_sql(f"CREATE INDEX ix_neorain_delay_info_{field} ON neorain_delay_info ({field})")
