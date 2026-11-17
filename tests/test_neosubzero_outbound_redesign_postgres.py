"""Independent reset/START transactions and additive legacy bootstrap."""
from unittest.mock import patch
from sqlalchemy import text
from app.extensions import db
from app.models import NeoSubZeroDepartureDeiceEvent as Event, NeoSubZeroAuditEntry as Audit
from app.neonodes.neosubzero import services as _subzero_services
from app.services.neosubzero_schema import ensure_neosubzero_pretreat_table
from tests import test_neosubzero_radio_postgres as fixtures


class OutboundResetPostgresTest(fixtures.RadioPostgresTest):
    def test_reset_and_start_have_one_winner_and_never_retire_actual_spray(self):
        event=Event.query.filter_by(sort_date_mission_id=self.f.alpha.id).one()
        eid=event.id
        reset=self.data(event,self.f.alpha,'reset')
        start=self.data(event,self.f.alpha,'save_treatment',treatment_plan='one_type_i',pass1_surface_area='wings_only',pass1_start='22:10')
        self.assertCountEqual(self.race(reset,start),[200,409])
        db.session.expire_all();event=db.session.get(Event,eid)
        self.assertNotEqual(event.reset_at_utc is None,event.pass1_started_at_utc is None)
        self.assertEqual(Audit.query.filter(Audit.action.in_(['departure_reset','departure_save_treatment'])).count(),1)

    def test_nullable_reset_bootstrap_is_idempotent_and_preserves_history(self):
        event=self.finish()
        before=event.id,event.tail_number,event.pass1_started_at_utc,event.pass1_ended_at_utc,event.updated_at,event.pass_evidence
        audits=Audit.query.count()
        db.session.remove()
        with db.engine.begin() as connection:
            connection.execute(text('ALTER TABLE neosubzero_departure_deice_events DROP COLUMN reset_at_utc'))
        with patch.dict(self.f.app.config,{'TESTING':False}):
            self.assertTrue(ensure_neosubzero_pretreat_table(self.f.app))
            self.assertTrue(ensure_neosubzero_pretreat_table(self.f.app))
        event=db.session.get(Event,before[0])
        self.assertEqual((event.id,event.tail_number,event.pass1_started_at_utc,event.pass1_ended_at_utc,event.updated_at,event.pass_evidence),before)
        self.assertIsNone(event.reset_at_utc)
        self.assertEqual(Audit.query.count(),audits)
