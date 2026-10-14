"""Raw PostgreSQL writers cannot bypass normalized flight identity protection."""
import os
import unittest
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from app.services.mission_identity import sync_mission_identity_schema, flight_identity

URI = os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@unittest.skipUnless(URI, 'Requires disposable loopback PostgreSQL')
class RainMissionIdentityPostgresTest(unittest.TestCase):
    def setUp(self):
        url = make_url(URI)
        if url.host not in ('127.0.0.1','localhost') or not url.database.startswith('neostaffing_test_'):
            raise RuntimeError('Refusing non-disposable database.')
        self.schema = 'rain_identity_' + uuid4().hex
        self.admin = create_engine(URI)
        with self.admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_engine(URI, connect_args={'options':f'-csearch_path={self.schema}'})
        with self.engine.begin() as conn:
            conn.execute(text('CREATE TABLE sort_date_operations (id integer PRIMARY KEY)'))
            conn.execute(text('CREATE TABLE sort_date_missions (id serial PRIMARY KEY, sort_date_operation_id integer REFERENCES sort_date_operations(id), flight_number text, mission_type text, notes text)'))
            conn.execute(text('INSERT INTO sort_date_operations VALUES (1),(2)'))
            sync_mission_identity_schema(conn)

    def tearDown(self):
        self.engine.dispose()
        with self.admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.admin.dispose()

    def insert(self, conn, flight, direction='arrival', operation=1):
        return conn.execute(text('INSERT INTO sort_date_missions(sort_date_operation_id,flight_number,mission_type) VALUES (:op,:flight,:direction) RETURNING id'),
            {'op':operation,'flight':flight,'direction':direction}).scalar_one()

    def test_alias_directions_edits_and_sort_isolation(self):
        with self.engine.begin() as conn:
            original = self.insert(conn, 'UPS0948')
            other = self.insert(conn, 'UPS950')
            self.insert(conn, 'UPS948', operation=2)
        with self.assertRaises(DBAPIError):
            with self.engine.begin() as conn:
                self.insert(conn, 'UPS948', 'departure')
        with self.assertRaises(DBAPIError):
            with self.engine.begin() as conn:
                conn.execute(text('UPDATE sort_date_missions SET flight_number=:flight WHERE id=:id'), {'flight':'5X0948','id':other})
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text('SELECT flight_number FROM sort_date_missions WHERE id=:id'),{'id':original}).scalar_one(),'UPS0948')

    def test_simultaneous_aliases_only_one_commits(self):
        barrier = Barrier(2)
        def write(flight):
            try:
                with self.engine.begin() as conn:
                    barrier.wait(timeout=5)
                    self.insert(conn,flight)
                return 'saved'
            except DBAPIError:
                return 'rejected'
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(write,flight) for flight in ('UPS0948','UPS948')]
            results = [future.result(timeout=12) for future in futures]
        self.assertCountEqual(results,['saved','rejected'])
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text('SELECT count(*) FROM sort_date_missions')).scalar_one(),1)

    def test_concurrent_edits_and_repeatable_read(self):
        for isolation in ('READ COMMITTED','REPEATABLE READ'):
            with self.engine.begin() as conn:
                conn.execute(text('DELETE FROM sort_date_missions'))
                ids = [self.insert(conn,f) for f in ('UPS100','UPS200')]
            barrier = Barrier(2)
            def edit(mid, flight):
                try:
                    with self.engine.connect().execution_options(isolation_level=isolation) as conn:
                        with conn.begin():
                            conn.execute(text('SELECT count(*) FROM sort_date_missions')).scalar()
                            barrier.wait(timeout=5)
                            conn.execute(text('UPDATE sort_date_missions SET flight_number=:flight WHERE id=:id'),{'id':mid,'flight':flight})
                    return 'saved'
                except DBAPIError:
                    return 'rejected'
            with ThreadPoolExecutor(2) as pool:
                futures = [pool.submit(edit,mid,f) for mid,f in zip(ids,('UPS0948','UPS948'))]
                self.assertCountEqual([f.result(timeout=12) for f in futures], ['saved','rejected'])

    def test_bootstrap_idempotent_and_historical_collisions_untouched(self):
        with self.engine.begin() as conn:
            conn.execute(text('DROP TRIGGER neo_mission_identity ON sort_date_missions'))
            ids = [self.insert(conn,flight) for flight in ('UPS0948','UPS948')]
            sync_mission_identity_schema(conn)
            sync_mission_identity_schema(conn)
            conn.execute(text("UPDATE sort_date_missions SET notes='historical retained'"))
            self.assertEqual(conn.execute(text('SELECT count(*) FROM sort_date_missions')).scalar_one(),2)
            for flight in ('UPS0948','ups948','5X0948','948','UPSHOT-N438UP'):
                self.assertEqual(conn.execute(text('SELECT neo_mission_flight_key(:f)'),{'f':flight}).scalar_one(),flight_identity(flight))
