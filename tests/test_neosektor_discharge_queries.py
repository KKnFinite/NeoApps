"""Fresh Discharge transaction budgets and operation-boundary equivalence."""
import unittest
from contextlib import nullcontext
from datetime import date, datetime, time, timedelta
from unittest.mock import patch

from sqlalchemy import event

from app.extensions import db
from app.models import NeoErmacUldRequest, NeoSektorUldOnTheWayEvent, SortDateOperation
from app.services import gateway_matrix, uld_requests, neosektor_live_refresh
from tests import test_neosektor_routes as fixtures


NOW = datetime(2026, 9, 11, 5, 30)


class Clock(datetime):
    @classmethod
    def utcnow(cls):
        return NOW

    @classmethod
    def now(cls, tz=None):
        return NOW.replace(tzinfo=tz)


class DischargeQueriesTest(unittest.TestCase):
    def workflow(self, *, reuse=True, current=True, size=2):
        fixture = fixtures.NeoSektorRoutesTest()
        fixture.setUp()
        try:
            fixture._login_approved_user('simulator')
            operation = fixture._add_sort_operation(date(2026, 9, 10), 'night') if current else None
            fixture._set_sort_window('night', time(22), time(2))
            fixture.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE'] = datetime(2026, 9, 11, 0, 30)
            rows = []
            for i in range(size):
                row = NeoErmacUldRequest(gateway_id=fixture.gateway.id,
                    sort_date_operation_id=operation.id if operation else None, door=f'D{i+1}',
                    a2_count=5, a1_count=3, amp_count=1, setup_needed=i%2==0,
                    created_at=NOW-timedelta(minutes=10+i), updated_at=NOW-timedelta(minutes=i))
                db.session.add(row)
                rows.append(row)
            db.session.add(NeoSektorUldOnTheWayEvent(gateway_id=fixture.gateway.id,
                sort_date_operation_id=operation.id if operation else None, door='D1', uld_type='A1', quantity=1,
                sent_at_utc=NOW-timedelta(minutes=1), expires_at_utc=NOW+timedelta(minutes=4)))
            db.session.commit()
            row_id = rows[0].id if rows else None
            original_cached = gateway_matrix.request_cached
            def no_reuse(namespace, key, resolve):
                return resolve() if namespace == 'gateway.discharge_operation_candidates' else original_cached(namespace, key, resolve)
            results = {}
            def measure(name, path, command=None, status=200):
                sql, boundaries = [], []
                def capture(conn, cursor, statement, params, context, many):
                    sql.append(' '.join(statement.lower().split()))
                def commit(conn):
                    boundaries.append(len(sql))
                with fixture.app.app_context():
                    engine = db.engine
                    event.listen(engine, 'before_cursor_execute', capture)
                    event.listen(engine, 'commit', commit)
                    try:
                        with nullcontext() if reuse else patch.object(gateway_matrix, 'request_cached', side_effect=no_reuse):
                            response = (fixture.client.get(path) if command is None
                                        else fixture.client.post(path, json=command))
                    finally:
                        event.remove(engine, 'before_cursor_execute', capture)
                        event.remove(engine, 'commit', commit)
                self.assertEqual(response.status_code, status, response.json if response.is_json else name)
                counts = tuple(sum(s.startswith(('select ', 'with ')) if verb=='select' else s.startswith(verb+' ')
                                   for s in sql) for verb in ('select','update','insert','delete'))
                payload = response.json if response.is_json else None
                results[name] = counts, payload, sql, boundaries
                return payload

            with patch.object(uld_requests, 'datetime', Clock), patch.object(neosektor_live_refresh, 'datetime', Clock), patch.object(
                    NeoErmacUldRequest.__table__.c.updated_at.onupdate, 'arg', lambda context: NOW):
                measure('cold_page','/neosektor/discharge')
                measure('page','/neosektor/discharge')
                initial = measure('state','/neosektor/discharge/state')
                measure('unchanged','/neosektor/discharge/state?revision='+initial['revision'])
                measure('changed','/neosektor/discharge/state?revision=stale')
                if rows:
                    path='/neosektor/discharge/send'
                    command={'request_id':row_id,'door':'D1','uld_type':'A2','quantity':1}
                    measure('invalid_type',path,{**command,'uld_type':'BAD'},400)
                    measure('zero',path,{**command,'quantity':0},400)
                    measure('wrong_door',path,{**command,'door':'D99'},400)
                    measure('missing_id',path,{**command,'request_id':999999},400)
                    measure('partial',path,command)
                    measure('multi_partial',path,{'request_id':row_id,'door':'D1','send_a2_count':1,'send_a1_count':1,'send_amp_count':1})
                    measure('already_zero_type',path,{**command,'uld_type':'AMP'})
                    complete = measure('complete',path,{'request_id':row_id,'door':'D1','send_a2_count':3,'send_a1_count':2,'send_amp_count':0})
                    self.assertNotIn(row_id, [r['id'] for r in complete['state']['requests']])
                    measure('stale_completed',path,command,400)
                    measure('post_complete','/neosektor/discharge/state')
                    # Completion removes the request, not its independent 5-minute messages.
                    events = NeoSektorUldOnTheWayEvent.query.order_by(NeoSektorUldOnTheWayEvent.id).all()
                    self.assertEqual(len(events), 8)
                    self.assertTrue(all(e.expires_at_utc-e.sent_at_utc == timedelta(minutes=5) for e in events))
            return results
        finally:
            fixture.tearDown()

    def test_query_write_budgets_payloads_and_postcommit_scope_are_equivalent(self):
        for current, size in ((True,2),(True,50),(True,0),(False,2)):
            before = self.workflow(reuse=False,current=current,size=size)
            after = self.workflow(reuse=True,current=current,size=size)
            for name, (counts,payload,sql,commits) in before.items():
                with self.subTest(current=current,size=size,path=name):
                    new_counts,new_payload,new_sql,new_commits = after[name]
                    success = name in ('partial','multi_partial','already_zero_type','complete')
                    failed_lookup = name in ('wrong_door','missing_id','stale_completed')
                    baseline = (29+int(not current) if name=='cold_page' else
                                18+int(not current) if name=='page' else
                                17+int(not current) if success else
                                10 if failed_lookup else
                                (5 if current else 4) if name=='unchanged' else
                                4 if name=='changed' and not current else 7)
                    saving = ((1 if current else 3) if name in ('cold_page','page') else
                              (1 if current else 4) if success else
                              int(not current) if failed_lookup else 0)
                    self.assertEqual(counts[0],baseline)
                    self.assertEqual(new_counts,(baseline-saving,*counts[1:]))
                    self.assertEqual(new_payload,payload)
                    self.assertEqual(len(new_commits),len(commits))
                    self.assertEqual(len(commits),int(success or name=='cold_page'))
                    writes = [s for s in sql if s.startswith(('update ','insert ','delete '))]
                    self.assertEqual([s for s in new_sql if s.startswith(('update ','insert ','delete '))],writes)
                    expected_writes = {'partial':(1,1,0),'multi_partial':(1,3,0),
                        'already_zero_type':(0,1,0),'complete':(0,2,1),'cold_page':(0,1,0)}
                    self.assertEqual(counts[1:],expected_writes.get(name,(0,0,0)))
                    candidate = 'from sort_date_operations where sort_date_operations.gateway_code ='
                    # Drop only duplicates WITHIN each transaction, never across commit.
                    expected,seen = [],set()
                    for index,statement in enumerate(sql):
                        if index in commits:
                            seen.clear()
                        if saving and candidate in statement:
                            if statement in seen:
                                continue
                            seen.add(statement)
                        expected.append(statement)
                    self.assertEqual(new_sql,expected)
                    if success:
                        self.assertEqual(sum(candidate in s for s in new_sql[:new_commits[0]]),1)
                        self.assertEqual(sum(candidate in s for s in new_sql[new_commits[0]:]),1)
                    print(f'{current}/{size}/{name}: SELECT,UPDATE,INSERT,DELETE {counts} -> {new_counts}')

    def test_send_response_resolves_new_sort_after_commit(self):
        fixture=fixtures.NeoSektorRoutesTest()
        fixture.setUp()
        try:
            fixture._login_approved_user('simulator')
            old=fixture._add_sort_operation(date(2026,9,10),'night')
            fixture._set_sort_window('night',time(22),time(2))
            fixture.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE']=datetime(2026,9,11,0,30)
            row=NeoErmacUldRequest(gateway_id=fixture.gateway.id,sort_date_operation_id=old.id,door='D1',a2_count=3)
            db.session.add(row)
            db.session.commit()
            old_id,gateway_id,row_id=old.id,fixture.gateway.id,row.id
            original_commit=db.session.commit
            new_ids=[]
            def commit_and_roll_sort():
                original_commit()
                # Another transaction changes current-sort identity before rendering.
                with db.engine.begin() as conn:
                    conn.execute(SortDateOperation.__table__.update().where(SortDateOperation.id==old_id).values(archived_at_utc=NOW))
                    new_id=conn.execute(SortDateOperation.__table__.insert().values(
                        gateway_id=gateway_id,gateway_code=fixture.gateway.code,sort_date=date(2026,9,11),sort_name='night')).inserted_primary_key[0]
                    conn.execute(NeoErmacUldRequest.__table__.insert().values(
                        gateway_id=gateway_id,sort_date_operation_id=new_id,door='D2',a2_count=9))
                    new_ids.append(new_id)
            with patch.object(db.session,'commit',side_effect=commit_and_roll_sort):
                response=fixture.client.post('/neosektor/discharge/send',json={
                    'request_id':row_id,'door':'D1','uld_type':'A2','quantity':1})
            self.assertEqual(response.status_code,200,response.json)
            self.assertEqual(response.json['state']['operation_id'],new_ids[0])
            self.assertEqual([r['door'] for r in response.json['state']['requests']],['D2'])
            self.assertEqual(response.json['event'],{'door':'D1','uld_type':'A2','quantity':1})
            self.assertEqual(NeoSektorUldOnTheWayEvent.query.one().sort_date_operation_id,old_id)
        finally:
            fixture.tearDown()

    def test_candidates_expire_at_rollback_and_request_boundary_not_window_selection(self):
        fixture = fixtures.NeoSektorRoutesTest()
        fixture.setUp()
        try:
            operation = fixture._add_sort_operation(date(2026, 9, 10), 'night')
            fixture._set_sort_window('night', time(22), time(2))
            operation_id = operation.id
            candidates = []

            def capture(conn, cursor, statement, params, context, many):
                if 'from sort_date_operations where sort_date_operations.gateway_code =' in ' '.join(statement.lower().split()):
                    candidates.append(statement)

            def current(hour):
                return [op.id for op in gateway_matrix.current_operations_for_gateway(
                    fixture.gateway, now=datetime(2026, 9, 11, hour, 30))]

            event.listen(db.engine, 'before_cursor_execute', capture)
            try:
                with fixture.app.test_request_context('/neosektor/discharge'):
                    self.assertEqual(current(0), [operation_id])
                    self.assertEqual(current(0), [operation_id])
                    # Window eligibility is NOT cached with the raw candidates.
                    self.assertEqual(current(3), [])
                    self.assertEqual(len(candidates), 1)
                    db.session.rollback()
                    self.assertEqual(current(0), [operation_id])
                    self.assertEqual(len(candidates), 2)
                with fixture.app.test_request_context('/neosektor/discharge'):
                    self.assertEqual(current(0), [operation_id])
                    self.assertEqual(len(candidates), 3)
            finally:
                event.remove(db.engine, 'before_cursor_execute', capture)
        finally:
            fixture.tearDown()
