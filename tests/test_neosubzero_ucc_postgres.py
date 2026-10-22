"""UCC transaction/upgrade proofs in UUID-scoped, loopback-only PostgreSQL."""
import json
import os
import re
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import create_app
from app.extensions import db
from app.models import Gateway, SortDateOperation, NeoSubZeroDepartureDeiceEvent, SortDateParkingAssignment, NeoSubZeroUccTruckAssignment
from app.services.neosubzero_schema import ensure_neosubzero_pretreat_table
from app.services.neosubzero_ucc import move_ucc_unit, ucc_staffing_version, set_neosubzero_ucc_assignment, reorder_ucc_queue, neosubzero_ucc_context, NeoSubZeroUccError
from tests import test_neosubzero_ucc as fixtures

URI = os.environ.get("NEOSTAFFING_TEST_POSTGRES_URL")


@unittest.skipUnless(URI, "Requires disposable local PostgreSQL")
class UccPostgresTest(unittest.TestCase):
    def setUp(self):
        url = make_url(URI)
        if url.host not in {"127.0.0.1", "localhost"} or not url.database.startswith("neostaffing_test_"):
            raise RuntimeError("Refusing a non-disposable PostgreSQL database")
        self.schema = "subzero_ucc_" + uuid4().hex
        self.engine = create_engine(URI)
        with self.engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.addCleanup(self.drop_schema)

        def app_for_schema(config):
            return create_app(type("PostgresUccConfig", (config,), {"SQLALCHEMY_DATABASE_URI": URI, "SQLALCHEMY_ENGINE_OPTIONS":{"connect_args":{"options":f"-csearch_path={self.schema}"}}}))

        self.fixture = fixtures.NeoSubZeroUccTest()
        with patch.object(fixtures, "create_app", app_for_schema):
            self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.ids = self.fixture.gateway.id, self.fixture.operation.id

    def drop_schema(self):
        if not re.fullmatch(r"subzero_ucc_[0-9a-f]{32}", self.schema):
            raise RuntimeError("Unexpected schema")
        with self.engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.engine.dispose()

    def race(self, callback):
        db.session.remove()
        def worker(index):
            with self.fixture.app.app_context():
                db.session.execute(text("SET LOCAL lock_timeout='5s'"))
                operation = SortDateOperation.query.filter_by(id=self.ids[1]).with_for_update().one()
                try:
                    callback(index, operation)
                    db.session.commit()
                    return "saved"
                except NeoSubZeroUccError:
                    db.session.rollback()
                    return "conflict"
                finally:
                    db.session.remove()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(worker, (0, 1)))

    def test_simultaneous_whole_unit_moves_have_exactly_one_winner(self):
        f = self.fixture
        set_neosubzero_ucc_assignment(f.operation,"Alpha",1,"driver",f.permanent)
        set_neosubzero_ucc_assignment(f.operation,"Alpha",1,"flyer",f.callout)
        db.session.add(NeoSubZeroUccTruckAssignment(sort_date_operation_id=f.operation.id,ramp="Alpha",position_number=1,truck_number="UCC-T1"))
        db.session.commit()
        version = ucc_staffing_version(f.operation)
        results = self.race(lambda i, op: move_ucc_unit(op,"Alpha",1,("Bravo","Charlie")[i],2,expected_version=version))
        self.assertCountEqual(results,["saved","conflict"])
        db.session.expire_all()
        from app.models import NeoSubZeroUccAssignment
        people = NeoSubZeroUccAssignment.query.filter(NeoSubZeroUccAssignment.person_id.is_not(None)).all()
        self.assertEqual(len(people),2)
        self.assertEqual(len({(p.ramp,p.position_number) for p in people}),1)
        self.assertEqual(NeoSubZeroUccTruckAssignment.query.filter_by(truck_number="UCC-T1").count(),1)

    def test_queue_corrections_are_serialized_and_do_not_change_physical_evidence(self):
        f = self.fixture
        second = f._departure("5X991", "N991", "SDF", 3)
        db.session.add(SortDateParkingAssignment(sort_date_operation_id=f.operation.id,tail_number="N991",ramp_code="A",position_code="A04"))
        for i, mission in enumerate((f.alpha,second)):
            mission.actual_block_out_datetime_utc=datetime(2026,9,1,1,i)
            f._deice_event(mission,"deice_planned")
        db.session.commit()
        ramp = next(r for r in neosubzero_ucc_context(f.gateway,f.operation)["ramps"] if r["name"]=="Alpha")
        version, mission_id = ramp["queue_version"], second.id
        results = self.race(lambda i, op: reorder_ucc_queue(db.session.get(Gateway,self.ids[0]),op,"Alpha",mission_id,"forward",expected_version=version,user_id=None))
        self.assertCountEqual(results,["saved","conflict"])
        db.session.expire_all()
        for event in NeoSubZeroDepartureDeiceEvent.query.all():
            self.assertEqual(len(json.loads(event.ucc_queue_history)),1)
            self.assertEqual(event.status,"deice_planned")
            self.assertIsNone(event.pass1_started_at_utc)
        self.assertEqual(NeoSubZeroDepartureDeiceEvent.query.count(),2)

    def test_additive_bootstrap_preserves_existing_event_and_is_idempotent(self):
        f = self.fixture
        event = f._deice_event(f.alpha,"deice_planned")
        db.session.commit()
        original = event.id, event.tail_number, event.updated_at
        db.session.remove()
        with db.engine.begin() as connection:
            connection.execute(text("ALTER TABLE neosubzero_departure_deice_events DROP COLUMN ucc_queue_order, DROP COLUMN ucc_queue_history"))
        with patch.dict(f.app.config,{"TESTING":False}):
            self.assertTrue(ensure_neosubzero_pretreat_table(f.app))
            self.assertTrue(ensure_neosubzero_pretreat_table(f.app))
        event = db.session.get(NeoSubZeroDepartureDeiceEvent,original[0])
        self.assertEqual((event.id,event.tail_number,event.updated_at),original)
        self.assertIsNone(event.ucc_queue_order)
        self.assertIsNone(event.ucc_queue_history)

    def test_pad_plus_three_survives_commits_and_clear_without_replacing_events(self):
        from datetime import datetime
        from app.services.neosubzero_departure_deice import mutate_departure_deice
        f=self.fixture
        missions=[f.alpha]
        for i in range(3):
            mission=f._departure(f'5X-PG{i}',f'N-PG{i}','SDF',3)
            db.session.add(SortDateParkingAssignment(sort_date_operation_id=f.operation.id,
                tail_number=mission.assigned_tail_number,ramp_code='A',position_code=f'A0{i+4}'))
            missions.append(mission)
        events=[]
        for i,mission in enumerate(missions):
            mission.actual_block_out_datetime_utc=datetime(2026,9,1,1,i)
            events.append(f._deice_event(mission,'deice_planned'))
        db.session.commit();ids=[event.id for event in events]
        before=neosubzero_ucc_context(f.gateway,f.operation)['ramps'][0]
        self.assertEqual(before['pad']['mission_id'],missions[0].id)
        self.assertEqual([row['mission_id'] for row in before['waiting_queue']],[m.id for m in missions[1:]])
        events[0].status='finished';events[0].pass1_ended_at_utc=datetime(2026,9,1,1,5)
        mutate_departure_deice(f.operation,missions[0],'clear',{},event=events[0]);db.session.commit()
        db.session.expire_all()
        after=neosubzero_ucc_context(f.gateway,f.operation)['ramps'][0]
        self.assertEqual(after['pad']['mission_id'],missions[1].id)
        self.assertEqual([row['queue_position'] for row in after['waiting_queue']],[1,2])
        self.assertEqual([event.id for event in NeoSubZeroDepartureDeiceEvent.query.order_by(NeoSubZeroDepartureDeiceEvent.id)],ids)
        self.assertEqual(db.session.get(NeoSubZeroDepartureDeiceEvent,ids[0]).pass1_ended_at_utc,datetime(2026,9,1,1,5))
