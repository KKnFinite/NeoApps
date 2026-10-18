"""Additive shared presentation setting on a disposable PostgreSQL schema."""
import unittest
from unittest.mock import patch
from sqlalchemy import text
from app.extensions import db
from app.models import NeoScorpionSettings, NeoScorpionFuelingEvent
from app.services.neoscorpion_schema import ensure_neoscorpion_production_schema
from tests import test_neoscorpion_segments_postgres as pg


@unittest.skipUnless(pg.URI,'Requires disposable local PostgreSQL')
class RowHeightPostgresTest(unittest.TestCase):
    setUp=pg.PhysicalSegmentPostgresTest.setUp
    drop_schema=pg.PhysicalSegmentPostgresTest.drop_schema
    prepare=pg.PhysicalSegmentPostgresTest.prepare

    def test_additive_default_repeat_preserves_other_settings_and_physical_work(self):
        self.settings.ready_assignment_pulse_enabled=False;db.session.commit()
        physical=(self.work.id,self.work.manual_on_at_utc,self.work.off_at_utc,self.work.tail_number)
        db.session.execute(text('ALTER TABLE neoscorpion_settings DROP COLUMN mission_row_height_percent'));db.session.commit()
        with patch.dict(self.fixture.app.config,{'TESTING':False}):
            for _ in range(2):ensure_neoscorpion_production_schema(self.fixture.app)
        db.session.expire_all()
        self.assertEqual(NeoScorpionSettings.query.one().mission_row_height_percent,100)
        self.assertFalse(self.settings.ready_assignment_pulse_enabled)
        self.settings.mission_row_height_percent=130;db.session.commit()
        with patch.dict(self.fixture.app.config,{'TESTING':False}):ensure_neoscorpion_production_schema(self.fixture.app)
        db.session.expire_all()
        self.assertEqual(self.settings.mission_row_height_percent,130)
        self.assertEqual((self.work.id,self.work.manual_on_at_utc,self.work.off_at_utc,self.work.tail_number),physical)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
