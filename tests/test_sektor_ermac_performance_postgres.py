"""Independent synthetic PostgreSQL proofs; never uses production DATABASE_URL."""
import os
import unittest
import statistics
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.sql.selectable import Select
from app import create_app
from app.extensions import db
from app.services import neosektor_live_refresh as revisions
from tests import test_sektor_performance as sektor
from tests import test_ermac_performance as ermac
from tests import test_neosektor_routes as sf
from tests import test_neoermac_routes as ef

URI=os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


def postgres(test, fixture, setup):
    url=make_url(URI)
    if url.host not in {'127.0.0.1','localhost'} or not url.database.startswith('neostaffing_test_'):
        raise RuntimeError('Refusing non-disposable PostgreSQL')
    schema='sektor_ermac_perf_'+uuid4().hex;admin=create_engine(URI,connect_args={'connect_timeout':5})
    with admin.begin() as connection:connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    def drop():
        with admin.begin() as connection:connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
    test.addCleanup(drop)
    def local(config):
        return create_app(type('OperationalPerformancePG',(config,),dict(SQLALCHEMY_DATABASE_URI=URI,
            SQLALCHEMY_ENGINE_OPTIONS={'connect_args':{'connect_timeout':5,
                'options':f'-csearch_path={schema} -clock_timeout=3000 -cstatement_timeout=15000'}})))
    with patch.object(fixture,'create_app',local):setup()


@unittest.skipUnless(URI,'Requires disposable loopback PostgreSQL')
class SektorPostgresPerformanceTest(sektor.SektorPerformanceTest):
    def setUp(self):postgres(self,sf,super().setUp)

    def test_postgres_plan_reads_sort_table_once_instead_of_per_union_branch(self):
        c=self.case;gateway=c.fixture.gateway;captured=[]
        from app.services.neosektor_live_counts import NeoSektorOperationalStateBundle as Bundle
        from app.models import NeoSektorSortState
        Bundle.load(gateway,sort_date=c.day,include_routing=True)
        db.session.add_all([NeoSektorSortState(gateway_id=gateway.id,gateway_code=gateway.code,
            sort_date=c.day-timedelta(days=i+1),sort_name='night') for i in range(300)])
        db.session.commit()
        def capture(conn,cur,sql,params,*args):
            if sql.startswith(('WITH sektor_revision_sort','SELECT')) and 'wave_timer:' not in sql:
                captured.append((sql,params))
        def plan(inline):
            captured.clear();event.listen(db.engine,'before_cursor_execute',capture)
            try:
                if inline:
                    with patch.object(Select,'cte',lambda query,name:query.subquery(name)):
                        revisions.neosektor_state_revision(gateway,'routing',sort_date=c.day)
                else:revisions.neosektor_state_revision(gateway,'routing',sort_date=c.day)
            finally:event.remove(db.engine,'before_cursor_execute',capture)
            sql,params=next((s,p) for s,p in reversed(captured) if 'sektor_revision_sort' in s)
            result=db.session.connection().exec_driver_sql('EXPLAIN (ANALYZE, FORMAT JSON) '+sql,params).scalar()
            def references(node):
                return int(node.get('Relation Name')=='neosektor_sort_states')+sum(references(n) for n in node.get('Plans',[]))
            return references(result[0]['Plan']),result[0]['Execution Time']
        old_times=[];new_times=[]
        for _ in range(7):
            before,old_ms=plan(True);after,new_ms=plan(False)
            old_times.append(old_ms);new_times.append(new_ms)
        self.assertGreaterEqual(before,7);self.assertEqual(after,1)
        print(dict(physical_sort_scans_before=before,physical_sort_scans_after=after,
                   execution_ms_before=statistics.median(old_times),execution_ms_after=statistics.median(new_times)))

    def test_independent_concurrent_readers_and_committed_update(self):
        c=self.case;f=c.fixture;app=f.app
        initial=f.client.get('/neosektor/driver-routing/state').json
        cookie=f.client.get_cookie('session').value;db.session.commit()
        barrier=Barrier(2);writes=[];pids=set();engine=db.engine
        def record(conn,cur,sql,*args):
            pids.add(conn.connection.driver_connection.get_backend_pid())
            if sql.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')):writes.append(sql)
        def read(_):
            with app.app_context():
                client=app.test_client();client.set_cookie('session',cookie);barrier.wait(10)
                return client.get('/neosektor/driver-routing/state?revision='+initial['revision']).json
        event.listen(engine,'before_cursor_execute',record)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(read,range(2)))
        finally:event.remove(engine,'before_cursor_execute',record)
        self.assertEqual(len(pids),2);self.assertEqual(writes,[])
        self.assertTrue(all(r['ok'] and r['changed'] is False for r in results))
        response=f.client.post('/neosektor/tunnel-conductor/wave',json={'wave':'first','value':16})
        self.assertEqual(response.status_code,200)
        changed=f.client.get('/neosektor/driver-routing/state?revision='+initial['revision']).json
        self.assertTrue(changed['changed']);self.assertNotEqual(changed['revision'],initial['revision'])


@unittest.skipUnless(URI,'Requires disposable loopback PostgreSQL')
class ErmacPostgresPerformanceTest(ermac.ErmacPerformanceTest):
    def setUp(self):postgres(self,ef,super().setUp)

    def test_competing_lineup_edits_keep_the_existing_atomic_conflict_guard(self):
        from app.models import Gateway,NeoErmacBuildingLineup
        from app.services.neoermac_building_lineup import slot_flight_value
        c=self.case;gateway=db.session.get(Gateway,c.gateway_id)
        field='lineup_green_runout_east_destination_1'
        forms=[c.flight_form(gateway,field,destination) for destination in ('ONT','PHX')]
        cookie=c.client.get_cookie('session').value;app=c.app
        db.session.commit();barrier=Barrier(2);pids=set()
        def record(conn,*args):pids.add(conn.connection.driver_connection.get_backend_pid())
        def save(form):
            with app.app_context():
                client=app.test_client();client.set_cookie('session',cookie);barrier.wait(10)
                return client.post('/neoermac/building-lineup/destination',data=form).status_code
        engine=db.engine;event.listen(engine,'before_cursor_execute',record)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(save,forms))
        finally:event.remove(engine,'before_cursor_execute',record)
        self.assertEqual(sorted(statuses),[200,409]);self.assertGreaterEqual(len(pids),2)
        db.session.expire_all();row=NeoErmacBuildingLineup.query.filter_by(gateway_id=c.gateway_id,runout_key='green_runout').one()
        self.assertIn(str(slot_flight_value(row,'east_destination_1')),{str(c.flight_ids['ONT']),str(c.flight_ids['PHX'])})
