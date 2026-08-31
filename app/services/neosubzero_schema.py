from sqlalchemy import inspect, text
from app.extensions import db
from app.models import NeoSubZeroPretreatState

LOCK_KEY = 7_483_327_341_930

def ensure_neosubzero_pretreat_table(app):
    uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    if app.config.get("TESTING") or not uri.startswith(("postgresql:", "postgresql+", "postgres:", "postgres+")):
        return False
    with app.app_context():
        try:
            with db.engine.connect() as read_connection:
                if inspect(read_connection).has_table(NeoSubZeroPretreatState.__tablename__):
                    return True
            connection = db.session.connection()
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_KEY})
            if not inspect(connection).has_table(NeoSubZeroPretreatState.__tablename__):
                NeoSubZeroPretreatState.__table__.create(bind=connection, checkfirst=False)
                db.session.commit()
            else:
                db.session.rollback()
        except Exception:
            db.session.rollback()
            raise
    return True
