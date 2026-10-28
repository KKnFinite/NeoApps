"""Real processes and timers; no browser, current sort or owner-PC scheduler."""
import multiprocessing
import os
import threading
import time
import unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch,Mock
from flask import Flask
from app.extensions import db
from app.models import FrostHistoryTask as Task
from app.services import frost_auto as auto
from tests import test_neosubzero_ucc_postgres as pg
from tests.test_frost_vault import MemoryR2


def capture_process(uri,schema,gateway_id,entered,release,results,heartbeat=False):
    app=Flask(__name__)
    app.config.update(TESTING=not heartbeat,SECRET_KEY='isolated',SQLALCHEMY_DATABASE_URI=uri,
        SQLALCHEMY_ENGINE_OPTIONS={'connect_args':{'options':f'-csearch_path={schema}'}})
    db.init_app(app);store=MemoryR2()
    def provider():
        entered.set()
        if not release.wait(20):raise RuntimeError('Isolated provider gate expired')
        from app.services.frost_weather import STATION
        return dict(station=STATION,cutoff_at_utc=auto.stamp(auto.now()),observations=[],sources=[],native_forecasts=[])
    real_timer=threading.Timer;count=[0]
    def timer(seconds,callback):
        count[0]+=1
        return real_timer(.05,callback) if count[0]==1 else Mock()
    with patch.object(auto,'acquire',side_effect=provider),patch.object(auto.jobs,'memory_allows_work',return_value=True),\
        patch('app.services.neoscorpion_learning_vault._require_r2_config',return_value={'bucket':'isolated'}),\
        patch('app.services.neoscorpion_learning_vault._r2_client',return_value=store):
        try:
            if heartbeat:
                from app.services.neoscorpion_spear_decisions import initialize_delivery_heartbeat
                with patch('app.services.neoscorpion_spear_decisions.threading.Timer',side_effect=timer),\
                    patch('app.services.neoscorpion_spear_decisions.drain',return_value=0),\
                    patch('app.services.neosubzero_archive.drain',return_value=0),\
                    patch('app.services.frost_documents.drain',return_value=0),\
                    patch('app.services.frost_xchange_links.drain',return_value=0):
                    initialize_delivery_heartbeat(app)
                    deadline=time.monotonic()+15
                    found=False
                    while time.monotonic()<deadline:
                        with app.app_context():
                            found=Task.query.filter_by(gateway_id=gateway_id,kind='auto_capture',status='ready').count()==1
                            db.session.remove()
                        if found:break
                        time.sleep(.05)
                    results.put((os.getpid(),int(found)))
            else:
                with app.app_context():results.put((os.getpid(),auto.tick(gateway_id)))
        finally:
            with app.app_context():db.session.remove();db.engine.dispose()


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class AutoProcessTest(unittest.TestCase):
    setUp=pg.UccPostgresTest.setUp
    drop_schema=pg.UccPostgresTest.drop_schema

    def test_two_processes_one_capture_and_existing_timer_without_browser(self):
        gateway_id=self.fixture.gateway.id
        db.session.remove();context=multiprocessing.get_context('spawn')
        entered,release,results=context.Event(),context.Event(),context.Queue()
        args=(pg.URI,self.schema,gateway_id,entered,release,results)
        first=context.Process(target=capture_process,args=args);second=context.Process(target=capture_process,args=args)
        try:
            first.start();self.assertTrue(entered.wait(20));second.start();second.join(20)
            self.assertFalse(second.is_alive());self.assertEqual(results.get(timeout=5)[1],0)
            release.set();first.join(20);self.assertEqual(results.get(timeout=5)[1],1)
            self.assertEqual((first.exitcode,second.exitcode),(0,0))
            self.assertEqual(Task.query.filter_by(kind='auto_capture',status='ready').count(),1)
        finally:
            release.set()
            for process in (first,second):
                if process.is_alive():process.terminate();process.join(5)
            results.close();results.join_thread()

    def test_real_timer_wakes_without_document_sort_request_or_manual_capture(self):
        # Fresh schema has no FrostDocument. No request or capture endpoint exists.
        from app.models import SortDateOperation,FrostDocument
        SortDateOperation.query.update(dict(archived_at_utc=datetime.now(timezone.utc).replace(tzinfo=None)))
        db.session.commit()
        self.assertEqual(SortDateOperation.query.filter_by(archived_at_utc=None).count(),0)
        self.assertEqual(FrostDocument.query.count(),0)
        gateway_id=self.fixture.gateway.id;db.session.remove()
        context=multiprocessing.get_context('spawn');entered,release,results=context.Event(),context.Event(),context.Queue()
        release.set();process=context.Process(target=capture_process,args=(pg.URI,self.schema,gateway_id,entered,release,results,True))
        try:
            process.start();process.join(25);self.assertFalse(process.is_alive())
            self.assertTrue(entered.is_set());self.assertEqual(results.get(timeout=5)[1],1)
            self.assertEqual(process.exitcode,0)
        finally:
            if process.is_alive():process.terminate();process.join(5)
            results.close();results.join_thread()


if __name__=='__main__':unittest.main()
