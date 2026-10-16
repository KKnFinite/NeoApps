"""Additive PostgreSQL upgrade, idempotent identity backfill and old data retention."""
import os
import unittest
from uuid import uuid4
from unittest.mock import patch
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.engine import make_url
from app import create_app
from app.extensions import db
from app.models import NeoErmacBuildingLineup, NeoErmacDoorPull
from app.services.schema_sync import sync_database_schema
from tests import test_neoermac_flight_identity as cases
from tests import test_neoermac_routes as fixtures

URI=os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL','')

@unittest.skipUnless(URI,'Requires disposable local PostgreSQL')
class FlightIdentityPostgresTest(unittest.TestCase):
    def test_old_lineup_schema_upgrade_twice_preserves_geometry_and_history(self):
        url=make_url(URI)
        if url.host not in {'localhost','127.0.0.1'} or not url.database.startswith('neostaffing_test_'):
            raise RuntimeError('Refusing non-disposable PostgreSQL')
        schema='ermac_identity_'+uuid4().hex; engine=create_engine(URI)
        with engine.begin() as conn:conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        def app_for_schema(config):
            return create_app(type('IdentityPGConfig',(config,),{'SQLALCHEMY_DATABASE_URI':URI,
                'SQLALCHEMY_ENGINE_OPTIONS':{'connect_args':{'options':f'-csearch_path={schema}'}}}))
        c=cases.FlightIdentityTest()
        try:
            with patch.object(fixtures,'create_app',app_for_schema):c.setUp()
            c.write(c.cards()[0]);db.session.commit()
            ids=(c.row.id,c.a.id,c.b.id,NeoErmacDoorPull.query.one().id)
            db.session.execute(text('ALTER TABLE neoermac_building_lineups DROP COLUMN flight_assignments'));db.session.commit()
            for _ in range(2):sync_database_schema(c.f.app);db.session.commit()
            db.session.expire_all()
            self.assertIn('flight_assignments',{col['name'] for col in inspect(db.engine).get_columns('neoermac_building_lineups')})
            row=db.session.get(NeoErmacBuildingLineup,ids[0])
            self.assertEqual(row.west_destination_1,'LAX')
            self.assertIsNone(row.flight_assignments['west_destination_1'])
            pull=db.session.get(NeoErmacDoorPull,ids[-1])
            self.assertEqual(pull.sort_date_mission_id,ids[2]);self.assertEqual(pull.actual_pure_pull_time_local.strftime('%H:%M'),'00:25')
        finally:
            if hasattr(c,'f'):c.doCleanups()
            with engine.begin() as conn:conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            engine.dispose()
