"""Canonical synthetic fuel work; no production records or providers."""
import json
import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import event

from app.extensions import db
from app.services import neoscorpion as fuel
from app.services.live_screen_refresh import live_poll_scope
from app.services.neoscorpion_dispatch_fragments import _CSRF
from tests import test_neoscorpion_fueler_submission as cases


class DispatchFragmentsTest(unittest.TestCase):
    setUp = cases.SubmissionTest.setUp
    prepare = cases.SubmissionTest.prepare
    login = cases.SubmissionTest.login
    save = cases.SubmissionTest.save
    final_data = cases.SubmissionTest.final_data
    off = cases.SubmissionTest.off
    submit = cases.SubmissionTest.submit
    baseline = cases.SubmissionTest.baseline

    def capture(self, previous=None, **extra):
        return self.client.get('/neoscorpion/fuel-dispatch/live-panel', query_string=dict(
            completion_only='1', delta='1', fingerprints=json.dumps(previous or {}), **extra))

    def ready(self):
        self.prepare()
        self.save(self.start+timedelta(minutes=10), **self.final_data())
        self.off(); self.submit(); self.login(self.dispatcher)
        self.client.get('/neoscorpion/fuel-dispatch/revision')

    def test_complete_form_and_full_initial_page_keep_canonical_identity(self):
        self.ready()
        result=self.capture().json
        self.assertTrue(result['compact'])
        self.assertIn('>COMPLETE</button>',result['html'])
        self.assertIn('expected_submission_version',result['html'])
        self.assertEqual(result['operation_id'],self.operation.id)
        self.assertIn('data-dispatch-preferences',self.client.get('/neoscorpion/fuel-dispatch').text)

    def test_ten_identical_critical_polls_omit_unchanged_mission_and_assets_markup(self):
        self.ready(); first=self.capture().json
        for _ in range(10):
            result=self.capture(first['fingerprints']).json
            self.assertNotIn('data-dispatch-mission-id',result['html'])
            self.assertNotIn('data-assets-workspace',result['html'])
            self.assertLess(len(result['html']),500)

    def test_changed_mission_is_sent_and_next_identical_poll_is_empty(self):
        self.ready(); first=self.capture().json
        self.mission.assigned_tail_number='N999UP';db.session.commit()
        result=self.capture(first['fingerprints']).json
        self.assertIn('N999UP',result['html'])
        self.assertNotIn('data-dispatch-mission-id',self.capture(result['fingerprints']).json['html'])

    def test_invalid_fingerprint_input_cannot_hide_work_or_crash(self):
        self.ready()
        for value in ('[]','not-json','x'*4000):
            result=self.client.get('/neoscorpion/fuel-dispatch/live-panel',query_string=dict(completion_only='1',delta='1',fingerprints=value))
            self.assertEqual(result.status_code,200)
            self.assertIn('>COMPLETE</button>',result.json['html'])

    def test_no_optional_spear_or_database_writes(self):
        self.ready(); writes=[]
        def record(c,cur,sql,*args):
            if sql.lstrip().split()[0].upper() in ('UPDATE','INSERT','DELETE'):writes.append(sql)
        event.listen(db.engine,'before_cursor_execute',record)
        try:
            with patch.object(fuel,'build_spear_plan',side_effect=AssertionError('No optional fitting/planning')):
                self.assertEqual(self.capture().status_code,200)
        finally:event.remove(db.engine,'before_cursor_execute',record)
        self.assertEqual(writes,[])

    def test_without_view_permission_no_fragment_is_returned(self):
        self.ready()
        with patch('app.neonodes.neoscorpion.routes.permission_access',return_value={'can_view':False,'can_edit':False}):
            response=self.capture()
        self.assertEqual(response.status_code,403)
        self.assertNotIn('html',response.json)

    def test_readonly_cannot_receive_complete_form(self):
        self.ready()
        with patch('app.neonodes.neoscorpion.routes.permission_access',return_value={'can_view':True,'can_edit':False}):
            response=self.capture()
        self.assertEqual(response.status_code,200)
        self.assertNotIn('>COMPLETE</button>',response.json['html'])

    def test_csrf_renewal_normalizes_checksum_only(self):
        old='<input type="hidden" name="csrf_token" value="old">'
        new='<input type="hidden" name="csrf_token" value="new">'
        self.assertEqual(_CSRF.sub(r'\1ignored\2',old),_CSRF.sub(r'\1ignored\2',new))
        self.assertIn('old',old) # Delivered forms are never rewritten by this hash helper.

    def test_scope_is_opaque_and_separates_user_session_and_gateway(self):
        self.ready()
        with self.fixture.app.test_request_context('/'):
            from flask_login import login_user
            login_user(self.dispatcher)
            one=live_poll_scope(self.gateway)
            self.assertEqual(len(one),64)
            self.assertEqual(one,live_poll_scope(self.gateway))
            from types import SimpleNamespace
            from flask import session
            self.assertNotEqual(one,live_poll_scope(SimpleNamespace(id=self.gateway.id+1)))
            session['_csrf_session_nonce']='another-isolated-session'
            self.assertNotEqual(one,live_poll_scope(self.gateway))
            session.pop('_csrf_session_nonce',None)
            original=self.dispatcher.auth_session_version
            self.dispatcher.auth_session_version+=1
            self.assertNotEqual(one,live_poll_scope(self.gateway))
            self.dispatcher.auth_session_version=original
            login_user(self.fueler)
            self.assertNotEqual(one,live_poll_scope(self.gateway))

    def test_wrong_sort_is_rejected_without_rendered_work(self):
        self.ready()
        result=self.capture(operation_id=str(self.operation.id+100))
        self.assertEqual(result.status_code,409)
        self.assertNotIn('html',result.json)

    def test_bounded_header_omits_unchanged_rows_without_a_large_query_url(self):
        self.ready();first=self.capture().json
        result=self.client.get('/neoscorpion/fuel-dispatch/live-panel?completion_only=1&delta=1',
            headers={'X-Neo-Dispatch-Fingerprints':json.dumps(first['fingerprints'])})
        self.assertEqual(result.status_code,200)
        self.assertNotIn('data-dispatch-mission-id',result.json['html'])
        scope=self.client.get('/neoscorpion/fuel-dispatch/revision').headers.get('X-Neo-Live-Scope')
        self.assertEqual(len(scope),64)
