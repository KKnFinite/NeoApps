"""Immutable TOP OFF milestones; a RETURN references one permanent SENT event."""
from sqlalchemy import DDL, event
from app.extensions import db


class NeoScorpionTopOffEvent(db.Model):
    __tablename__ = 'neoscorpion_top_off_events'
    __table_args__ = (
        db.CheckConstraint("kind IN ('sent','returned')", name='ck_scorpion_top_off_kind'),
        db.CheckConstraint("(kind = 'sent' AND sent_event_id IS NULL) OR kind = 'returned'",
                           name='ck_scorpion_top_off_reference'),
        db.CheckConstraint('gallons IS NULL OR gallons >= 0', name='ck_scorpion_top_off_gallons'),
        db.UniqueConstraint('sent_event_id', name='uq_scorpion_top_off_return'),
    )
    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(db.Integer, db.ForeignKey('sort_date_operations.id'), nullable=False, index=True)
    fuel_truck_id = db.Column(db.Integer, db.ForeignKey('neoscorpion_fuel_trucks.id'), nullable=False, index=True)
    kind = db.Column(db.String(12), nullable=False)
    sent_event_id = db.Column(db.Integer, db.ForeignKey('neoscorpion_top_off_events.id'))
    occurred_at_utc = db.Column(db.DateTime, nullable=False)
    gallons = db.Column(db.Integer)
    inventory_verified = db.Column(db.Boolean, nullable=False)
    inventory_epoch = db.Column(db.Integer, nullable=False)
    actor_user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    driver_name = db.Column(db.String(160), nullable=False, default='UNKNOWN')


def _immutable(*_args):
    raise ValueError('TOP OFF history is append-only.')


event.listen(NeoScorpionTopOffEvent, 'before_update', _immutable)
event.listen(NeoScorpionTopOffEvent, 'before_delete', _immutable)
# Protect history from direct SQL as well as ORM edits. No existing rows are changed.
event.listen(NeoScorpionTopOffEvent.__table__, 'after_create', DDL("""
CREATE OR REPLACE FUNCTION scorpion_top_off_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'TOP OFF history is append-only'; END $$;
CREATE TRIGGER scorpion_top_off_immutable BEFORE UPDATE OR DELETE ON neoscorpion_top_off_events
FOR EACH ROW EXECUTE FUNCTION scorpion_top_off_immutable();
""").execute_if(dialect='postgresql'))
for operation in ('UPDATE', 'DELETE'):
    event.listen(NeoScorpionTopOffEvent.__table__, 'after_create', DDL(f"""
    CREATE TRIGGER scorpion_top_off_no_{operation.lower()} BEFORE {operation} ON neoscorpion_top_off_events
    BEGIN SELECT RAISE(ABORT, 'TOP OFF history is append-only'); END;
    """).execute_if(dialect='sqlite'))
