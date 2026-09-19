"""Additive finalization compatibility, run only by schema/bootstrap handling."""
from sqlalchemy import inspect, text

from app.extensions import db
from app.models.staffing_employee_record import StaffingEmployeeRecord


def ensure_employee_record_delivery():
    table = StaffingEmployeeRecord.__table__
    name = "ck_employee_record_finalization"
    constraint = next(item for item in table.constraints if item.name == name)
    with db.engine.connect() as conn:
        checks = inspect(conn).get_check_constraints(table.name)
        existing = next((item["sqltext"] for item in checks if item["name"] == name), "")
        if "'delivered'" in existing:
            return
        if conn.dialect.name == "postgresql":
            # Both statements commit together. Old signature/RTS rows stay valid;
            # no original or append-only event is rewritten.
            conn.execute(text(f"ALTER TABLE {table.name} DROP CONSTRAINT IF EXISTS {name}"))
            conn.execute(text(f"ALTER TABLE {table.name} ADD CONSTRAINT {name} CHECK ({constraint.sqltext})"))
            conn.commit()
        elif conn.dialect.name == "sqlite":
            # SQLite cannot ALTER CHECK. Rebuild only this table, preserving all
            # data, indexes and immutable triggers, with FK validation before commit.
            ddl = conn.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table.name,)).scalar_one()
            old = "(acknowledgment = 'rts' AND signature_key IS NULL)"
            if old not in ddl:
                raise RuntimeError("Unrecognized Employee Record constraint; manual schema review required.")
            objects = conn.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL",
                (table.name,),
            ).scalars().all()
            foreign_keys = conn.exec_driver_sql("PRAGMA foreign_keys").scalar()
            conn.commit()
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            conn.commit()
            try:
                conn.exec_driver_sql("BEGIN IMMEDIATE")
                temporary = "staffing_employee_records_delivery_upgrade"
                updated = ddl.replace(table.name, temporary, 1).replace(
                    old, "(acknowledgment IN ('rts','delivered') AND signature_key IS NULL)", 1)
                conn.exec_driver_sql(updated)
                conn.exec_driver_sql(f"INSERT INTO {temporary} SELECT * FROM {table.name}")
                conn.exec_driver_sql(f"DROP TABLE {table.name}")
                conn.exec_driver_sql(f"ALTER TABLE {temporary} RENAME TO {table.name}")
                for statement in objects:
                    conn.exec_driver_sql(statement)
                if conn.exec_driver_sql("PRAGMA foreign_key_check").first():
                    raise RuntimeError("Employee Record upgrade failed foreign-key validation.")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.exec_driver_sql(f"PRAGMA foreign_keys={int(foreign_keys)}")
                conn.commit()
        else:
            raise RuntimeError("Unsupported Employee Record schema dialect.")
