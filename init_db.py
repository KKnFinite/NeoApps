from app import create_app
from app.extensions import db
from app.models import User
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.schema_sync import sync_local_sqlite_schema


app = create_app()


with app.app_context():
    db.create_all()
    sync_local_sqlite_schema(app)
    ensure_default_gateway_and_nodes()
    db.session.commit()
    print("Database tables created.")
