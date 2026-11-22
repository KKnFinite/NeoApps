"""Existing real Fueler/Dispatch and Staffing saves, with original tokens aged."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit
from flask import current_app, g, has_request_context, request
import time
import unittest
from tests.browser import test_mobile_drawer as fixtures
from tests.browser import test_neoscorpion_submission as fuel
from tests.browser import test_neostaffing_employee_editor as staffing


@contextmanager
def aged_screens():
    state={'renewed':0};observed=set();initial_pages=set()
    original=fixtures.MobileDrawerBrowserTest.ready
    def ready(instance,page,path):
        initial_pages.add(urlsplit(path).path)
        if id(page) not in observed:
            observed.add(id(page))
            def response(result):
                if '/csrf-token' in result.url and result.status==200:state['renewed']+=1
            page.on('response',response)
        original(instance,page,path)
    def issuance_time():
        if not has_request_context():
            return time.time()
        clock = current_app.config.get('CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE')
        now = clock.timestamp() if clock and clock.tzinfo else time.time()
        # Age only the original signed form tokens. Renewal and validation see
        # current time; the operational clock stays inside its configured Sort.
        if request.method == 'GET' and request.path in initial_pages:
            initial_pages.remove(request.path)
            g.age_initial_csrf_token = True
        return now - 3 * 3600 if g.get('age_initial_csrf_token') else now
    with patch('app.services.csrf.time',SimpleNamespace(time=issuance_time)),patch.object(fixtures.MobileDrawerBrowserTest,'ready',ready):
        yield state


class OperationalShiftCsrfBrowserTest(unittest.TestCase):
    def test_existing_chromium_webkit_fueler_and_dispatch_workflows_after_expiry(self):
        with aged_screens() as evidence:
            fuel.SubmissionBrowserTest().test_chromium_webkit_preparation_off_handoff_and_correction()
        self.assertGreaterEqual(evidence['renewed'],2)

    def test_existing_staffing_editor_validation_and_saves_after_expiry(self):
        with aged_screens() as evidence:
            staffing.EmployeeEditorBrowserTest.setUpClass()
            try:staffing.EmployeeEditorBrowserTest().test_desktop_same_editor_atomic_error_close_and_return_filters_scroll()
            finally:staffing.EmployeeEditorBrowserTest.tearDownClass()
        self.assertGreaterEqual(evidence['renewed'],1)
