"""Append-only security history, including bulk SQL; no grants or data backfill."""
from sqlalchemy import text


def ensure_account_security_history(connection, tables=("account_security_events",)):
    allowed = {"account_security_events", "account_access_decisions"}
    if not set(tables) <= allowed:
        raise ValueError("Unsupported security history table.")
    if connection.dialect.name == "sqlite":
        for table in tables:
            for action in ("UPDATE", "DELETE"):
                connection.execute(text(
                    f"CREATE TRIGGER IF NOT EXISTS {table}_immutable_{action.lower()} "
                    f"BEFORE {action} ON {table} BEGIN "
                    "SELECT RAISE(ABORT, 'Account security history is immutable'); END"))
    elif connection.dialect.name == "postgresql":
        connection.execute(text("""CREATE OR REPLACE FUNCTION neoapps_reject_security_history_change()
            RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'Account security history is immutable'; END $$"""))
        for table in tables:
            connection.execute(text(f"""DO $$ BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='{table}'::regclass
                AND tgname='{table}_immutable') THEN
                CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION neoapps_reject_security_history_change();
              END IF;
              IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='{table}'::regclass
                AND tgname='{table}_no_truncate') THEN
                CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table}
                FOR EACH STATEMENT EXECUTE FUNCTION neoapps_reject_security_history_change();
              END IF;
            END $$"""))
    else:
        raise RuntimeError("Security history requires SQLite or PostgreSQL.")
