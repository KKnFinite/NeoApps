"""Measured read optimization without changing operational evidence or authority."""
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from flask import g
from sqlalchemy import event, select
from sqlalchemy.sql.selectable import Select
from app.extensions import db
from app.models import NeoSektorSortState, SortDateOperation
from app.services import neosektor_live_refresh as revisions, operation_lifecycle
from app.services.operational_request_policy import operation_read_options
from tests import test_neosektor_poll_queries as fixtures


class SektorPerformanceTest(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.PollQueriesTest()
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def test_cte_preserves_exact_revisions_and_timer_boundaries(self):
        c=self.case;f=c.fixture;gateway=f.gateway
        for populated in (False, True):
            if populated:
                from app.services.neosektor_live_counts import NeoSektorOperationalStateBundle as Bundle
                bundle=Bundle.load(gateway,sort_date=c.day,include_routing=True)
                bundle.waves[0].all_up_started_at=datetime(2026,9,11,3)
                bundle.waves[0].planned_count=20
                db.session.commit()
            for scope in ('counts','routing'):
                for at in (datetime(2026,9,11,3,14,59),datetime(2026,9,11,3,15)):
                    with self.subTest(populated=populated,scope=scope,at=at):
                        args=dict(sort_date=c.day,now_utc=at)
                        actual=revisions.neosektor_state_revision(gateway,scope,**args)
                        # Inline the exact same scoped row instead of sharing it.
                        # All aggregate values and timestamp handling must match.
                        with patch.object(Select,'cte',lambda query,name:query.subquery(name)):
                            reference=revisions.neosektor_state_revision(gateway,scope,**args)
                        self.assertEqual(actual,reference)

    def test_scoped_historical_lookup_uses_one_physical_sort_reference(self):
        c=self.case;g=c.fixture.gateway
        db.session.add_all([NeoSektorSortState(gateway_id=g.id,gateway_code=g.code,
            sort_date=c.day-timedelta(days=i+1),sort_name='night') for i in range(300)])
        db.session.commit();statements=[]
        def capture(conn,cur,sql,*args):statements.append(sql)
        event.listen(db.engine,'before_cursor_execute',capture)
        try:revisions.neosektor_state_revision(g,'routing',sort_date=c.day)
        finally:event.remove(db.engine,'before_cursor_execute',capture)
        query=next(s for s in statements if s.startswith('WITH sektor_revision_sort'))
        self.assertEqual(query.count('FROM neosektor_sort_states'),1)
        self.assertNotIn('OFFSET',query.upper())

    def test_driver_and_conductor_lifecycle_reuse_is_exactly_one_fewer_select(self):
        c=self.case
        for path in ('/neosektor/driver-routing/state','/neosektor/tunnel-conductor/state'):
            with self.subTest(path=path):
                before,old=c.measure(path,legacy=True)
                after,new=c.measure(path)
                self.assertEqual(before,after)
                self.assertEqual(len(old),len(new)+1)
                self.assertEqual(sum('left outer join sort_timeline_sort_settings' in s for s in new),2)

    def test_snapshots_stay_persisted_but_are_not_loaded_by_state_or_page_reads(self):
        c=self.case;f=c.fixture;opid=c.operation.id
        # Initial page/setup may initialize canonical defaults; measure warm
        # read-only screens, as the existing page/poll contract requires.
        self.assertEqual(f.client.get('/neosektor/driver-routing').status_code,200)
        original=json.dumps({'provider_history':'x'*16000})
        c.operation.flight_api_last_poll_snapshot_json=original;db.session.commit()
        for path in ('/neosektor/driver-routing/state','/neosektor/tunnel-conductor/state',
                     '/neosektor/ballmat/state?side=east','/neosektor/driver-routing'):
            with self.subTest(path=path):
                g.__dict__.clear();db.session.remove();statements=[]
                def capture(conn,cur,sql,*args):statements.append(sql)
                event.listen(db.engine,'before_cursor_execute',capture)
                try:response=f.client.get(path)
                finally:event.remove(db.engine,'before_cursor_execute',capture)
                self.assertEqual(response.status_code,200)
                self.assertFalse(any('flight_api_last_poll_snapshot_json' in s for s in statements))
                self.assertFalse(any(s.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for s in statements))
        self.assertEqual(db.session.get(SortDateOperation,opid).flight_api_last_poll_snapshot_json,original)

    def test_projection_is_read_only_opt_in_and_deferred_history_remains_available(self):
        c=self.case;f=c.fixture;opid=c.operation.id
        c.operation.flight_api_last_poll_snapshot_json='original historical provider evidence';db.session.commit()
        db.session.remove()
        self.assertEqual(operation_read_options(),())
        for path,method in (('/neoscorpion/fueler','GET'),('/neosektor/ballmat/state','POST'),('/neoermac/building-lineup','POST')):
            with f.app.test_request_context(path,method=method):self.assertEqual(operation_read_options(),())
        with f.app.test_request_context('/neosektor/driver-routing/state'):
            operation=db.session.query(SortDateOperation).options(*operation_read_options()).filter_by(id=opid).one()
            self.assertNotIn('flight_api_last_poll_snapshot_json',operation.__dict__)
            self.assertEqual(operation.flight_api_last_poll_snapshot_json,'original historical provider evidence')

    def test_only_authorized_successful_routing_hints_advertise_the_session_partition(self):
        c=self.case;f=c.fixture
        path='/neosektor/driver-routing/version?sort_date='+str(c.day)+'&sort_name=night'
        response=f.client.get(path)
        self.assertEqual(response.status_code,200)
        self.assertRegex(response.headers['X-Neo-Live-Scope'],r'^[a-f0-9]{64}$')
        invalid=f.client.get('/neosektor/driver-routing/version?sort_date=invalid')
        self.assertEqual(invalid.status_code,400)
        self.assertNotIn('X-Neo-Live-Scope',invalid.headers)
        with patch('app.neonodes.neosektor.routes.user_can',return_value=False):
            denied=f.client.get(path)
        self.assertEqual(denied.status_code,403)
        self.assertNotIn('X-Neo-Live-Scope',denied.headers)
