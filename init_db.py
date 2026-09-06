"""Local SQLite convenience only. Production: python scripts/bootstrap_database.py."""

from app import create_app
from app.extensions import db
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.schema_sync import sync_local_sqlite_schema


app = create_app()
if not str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).startswith("sqlite:"):
    raise RuntimeError("For production use: python scripts/bootstrap_database.py")


with app.app_context():
    db.create_all()
    sync_local_sqlite_schema(app)
    ensure_default_gateway_and_nodes()
    db.session.commit()
    print("Database tables created.")
