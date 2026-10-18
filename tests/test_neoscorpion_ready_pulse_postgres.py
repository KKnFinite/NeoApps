"""Additive boolean migration on disposable PostgreSQL, preserving physical history."""
import unittest
from unittest.mock import patch
from datetime import timedelta
from sqlalchemy import text
from app.extensions import db
from app.models import NeoScorpionFuelingEvent, NeoScorpionSettings, NeoScorpionSortTruck
from app.services import neoscorpion as fuel
from app.services.neoscorpion_schema import ensure_neoscorpion_production_schema
from tests import test_neoscorpion_segments_postgres as pg


@unittest.skipUnless(pg.URI,'Requires disposable local PostgreSQL')
class ReadyPulsePostgresTest(unittest.TestCase):
    setUp=pg.PhysicalSegmentPostgresTest.setUp
    drop_schema=pg.PhysicalSegmentPostgresTest.drop_schema
    prepare=pg.PhysicalSegmentPostgresTest.prepare
    save=pg.PhysicalSegmentPostgresTest.save
    off=pg.PhysicalSegmentPostgresTest.off
    final_data=pg.PhysicalSegmentPostgresTest.final_data

    def test_additive_default_and_repeat_never_rewrite_events_or_explicit_off(self):
        self.save(self.start+timedelta(minutes=10),**self.final_data());self.off()
        row=fuel.fueler_context(self.gateway,self.fueler,assignment_id=self.assignment.id)['rows'][0]
        fuel.submit_fueler_assignment(self.gateway,self.fueler,self.assignment.id,expected=row['edit_baseline'])
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        event=NeoScorpionFuelingEvent.query.one()
        evidence=(event.id,event.fuel_work_state_id,event.started_at_utc,event.ended_at_utc,event.transfer_fuel_gallons)
        balance=self.selection.current_gallons
        db.session.execute(text('ALTER TABLE neoscorpion_settings DROP COLUMN ready_assignment_pulse_enabled'))
        db.session.commit()
        with patch.dict(self.fixture.app.config,{'TESTING':False}):
            for _ in range(2):ensure_neoscorpion_production_schema(self.fixture.app)
        db.session.expire_all();settings=NeoScorpionSettings.query.one()
        self.assertTrue(settings.ready_assignment_pulse_enabled)
        settings.ready_assignment_pulse_enabled=False;db.session.commit()
        with patch.dict(self.fixture.app.config,{'TESTING':False}):
            ensure_neoscorpion_production_schema(self.fixture.app)
        db.session.expire_all();self.assertFalse(NeoScorpionSettings.query.one().ready_assignment_pulse_enabled)
        event=NeoScorpionFuelingEvent.query.one()
        self.assertEqual((event.id,event.fuel_work_state_id,event.started_at_utc,event.ended_at_utc,event.transfer_fuel_gallons),evidence)
        self.assertEqual(NeoScorpionSortTruck.query.filter_by(fuel_truck_id=self.truck.id).one().current_gallons,balance)
