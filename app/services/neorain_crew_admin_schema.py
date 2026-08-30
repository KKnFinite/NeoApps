from sqlalchemy import text
from app.extensions import db
from app.models import NeoRainCrewAdminAssignment

def ensure_neorain_crew_admin_assignments_table(app):
    if app.config.get("TESTING") or not str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower().startswith(("postgresql:", "postgresql+", "postgres:", "postgres+")):
        return False
    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(text("SELECT pg_advisory_xact_lock(7483327341920)"))
            NeoRainCrewAdminAssignment.__table__.create(bind=connection, checkfirst=True)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
    return True
