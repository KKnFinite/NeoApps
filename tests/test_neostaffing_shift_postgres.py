"""Opt-in PostgreSQL proof, disposable loopback schemas only."""
import os
import unittest
from unittest.mock import patch
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import create_app
from app.extensions import db
from tests import test_neostaffing_shift_authority as fixtures

URI = os.environ.get("NEOSTAFFING_TEST_POSTGRES_URL")


@unittest.skipUnless(URI, "Requires disposable NEOSTAFFING_TEST_POSTGRES_URL")
class ShiftPostgresTest(fixtures.ShiftAuthorityTest):
    def setUp(self):
        url = make_url(URI)
        if url.get_backend_name() != "postgresql" or url.host not in ("localhost", "127.0.0.1") or not url.database.startswith("neostaffing_test_"):
            raise ValueError("Only loopback neostaffing_test_* databases are allowed")
        self.schema = "shift_" + uuid4().hex
        self.admin = create_engine(URI)
        with self.admin.begin() as connection:
            connection.execute(text("CREATE SCHEMA " + self.schema))
        def pg_app(config):
            config.SQLALCHEMY_DATABASE_URI = URI
            config.SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"options": "-csearch_path=" + self.schema + " -clock_timeout=10000 -cstatement_timeout=30000"}}
            return create_app(config, auto_bootstrap=False)
        with patch.object(fixtures, "create_app", side_effect=pg_app):
            super().setUp()

    def tearDown(self):
        db.session.remove(); db.engine.dispose(); self.context.pop()
        with self.admin.begin() as connection:
            connection.execute(text("DROP SCHEMA " + self.schema + " CASCADE"))
        self.admin.dispose()

    def test_concurrent_same_sort_insert_has_one_winner(self):
        person = self.person("full_time_combo")
        person_id = person.id
        area_ids = [self.areas[name].id for name in ("Door 6", "Door 9")]
        db.session.commit()
        barrier = Barrier(2)
        def insert(area_id):
            try:
                with self.admin.begin() as connection:
                    connection.execute(text("SET LOCAL search_path TO " + self.schema))
                    connection.execute(text("SET LOCAL lock_timeout='10s'"))
                    barrier.wait(timeout=10)
                    connection.execute(text("INSERT INTO staffing_work_assignments(person_id,work_area_unit_id,active,created_at,updated_at) VALUES (:p,:a,true,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"), {"p": person_id, "a": area_id})
                return True
            except Exception:
                return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sorted(executor.map(insert, area_ids)), [False, True])
        self.assertEqual(db.session.execute(text("SELECT count(*) FROM staffing_work_assignments WHERE person_id=:p"), {"p": person_id}).scalar(), 1)

    def test_concurrent_complete_routes_reject_stale_home_and_plan_together(self):
        from app.models import StaffingPerson, StaffingUnit
        from app.services import neostaffing as staffing
        person = self.person('full_time_combo')
        staffing.assign_work_area(person, self.other)
        staffing.assign_work_area(person, self.areas['Door 6']); db.session.commit()
        person_id, original = person.id, self.revision(person)
        doors = [self.areas[name].id for name in ('Door 24', 'Door 9')]
        home_id, other_id = self.areas['Door 6'].id, self.other.id
        db.session.commit()
        barrier = Barrier(2)
        def move(door_id):
            with self.app.app_context():
                local_person = db.session.get(StaffingPerson, person_id)
                area = db.session.get(StaffingUnit, home_id)
                # Both requests have read the original Home before either locks.
                staffing.assignment_service.shift_home(local_person)
                barrier.wait(timeout=10)
                result = staffing.move_shift_flow_final_composite(local_person, door_id, 'bm2', area, original, complete_route=True)
                if 'conflict' in result:
                    db.session.rollback(); return 'conflict'
                db.session.commit(); return 'changed'
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sorted(executor.map(move, doors)), ['changed','conflict'])
        db.session.expire_all()
        plan = person.shift_flow_plan
        home = staffing.assignment_service.shift_home(person)
        self.assertEqual(plan.sort_start_work_area_id, home.work_area_unit_id)
        self.assertEqual(home.work_area.name, 'West Ballmat' if plan.final_door_work_area_id == doors[0] else 'East Ballmat')
        self.assertIn(other_id, [a.work_area_unit_id for a in person.work_assignments])
