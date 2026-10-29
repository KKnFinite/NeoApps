"""A real bootstrap-style DDL deadlock, only in a disposable loopback schema."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url
from app import create_app
from app.extensions import db
from app.services.database_startup_retry import run_startup_database_action

URI=os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')

@unittest.skipUnless(URI,'Requires disposable loopback PostgreSQL')
class StartupDeadlockPostgresTest(unittest.TestCase):
    def test_deadlock_rolls_back_everything_then_replays_complete_action(self):
        url=make_url(URI)
        if url.host not in ('127.0.0.1','localhost') or not (url.database or '').startswith('neostaffing_test_'):
            raise ValueError('Disposable loopback database required')
        schema='startup_deadlock_'+uuid4().hex;admin=create_engine(URI)
        with admin.begin() as connection:connection.execute(text('CREATE SCHEMA '+schema))
        app=create_app(type('C',(),dict(TESTING=True,SECRET_KEY='isolated-deadlock',SQLALCHEMY_DATABASE_URI=URI,
            SQLALCHEMY_ENGINE_OPTIONS={'connect_args':{'options':'-csearch_path='+schema+' -clock_timeout=5000 -cstatement_timeout=10000'}},
            DATABASE_STARTUP_RETRY_ATTEMPTS_TESTING=3,DATABASE_STARTUP_RETRY_INITIAL_DELAY_SECONDS=0)))
        engine=None
        try:
            with app.app_context():
                engine=db.engine
                for name in ('a','b','attempts'):db.session.execute(text(f'CREATE TABLE {name} (id integer PRIMARY KEY)'))
                db.session.commit();b_locked=Event();a_locked=Event();competing=Event();attempts=[]
                def traffic():
                    with engine.begin() as connection:
                        connection.execute(text("SET LOCAL deadlock_timeout='5s'"))
                        connection.execute(text('LOCK TABLE b IN ACCESS SHARE MODE'));b_locked.set()
                        if not a_locked.wait(10):raise RuntimeError('Fixture synchronization failed')
                        competing.set();connection.execute(text('LOCK TABLE a IN ACCESS SHARE MODE'))
                def action():
                    attempts.append(len(attempts)+1)
                    db.session.execute(text("SET LOCAL deadlock_timeout='100ms'"))
                    db.session.execute(text('LOCK TABLE a IN ACCESS EXCLUSIVE MODE'))
                    db.session.execute(text('INSERT INTO attempts VALUES (:id)'),dict(id=attempts[-1]))
                    if len(attempts)==1:
                        a_locked.set()
                        if not competing.wait(10):raise RuntimeError('Fixture synchronization failed')
                    db.session.execute(text('LOCK TABLE b IN ACCESS EXCLUSIVE MODE'))
                    db.session.commit();return 'complete'
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future=pool.submit(traffic)
                    self.assertTrue(b_locked.wait(10))
                    self.assertEqual(run_startup_database_action(app,action,action_name='isolated DDL fixture'),'complete')
                    future.result(timeout=15)
                self.assertEqual(attempts,[1,2])
                self.assertEqual(db.session.execute(text('SELECT id FROM attempts')).scalars().all(),[2])
                db.session.remove();engine.dispose()
        finally:
            if engine is not None:engine.dispose()
            with admin.begin() as connection:connection.execute(text('DROP SCHEMA '+schema+' CASCADE'))
            admin.dispose()
