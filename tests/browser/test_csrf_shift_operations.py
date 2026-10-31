"""Existing real Fueler/Dispatch and Staffing saves, with original tokens aged."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
import time
import unittest
from tests.browser import test_mobile_drawer as fixtures
from tests.browser import test_neoscorpion_submission as fuel
from tests.browser import test_neostaffing_employee_editor as staffing


@contextmanager
def aged_screens():
    state={'seconds':0,'renewed':0};observed=set()
    original=fixtures.MobileDrawerBrowserTest.ready
    def ready(instance,page,path):
        original(instance,page,path)
        if id(page) not in observed:
            observed.add(id(page))
            def response(result):
                if '/csrf-token' in result.url and result.status==200:state['renewed']+=1
            page.on('response',response)
        state['seconds']+=3*3600
        for tab in page.context.pages:
            if tab.url.startswith(fixtures.MobileDrawerBrowserTest.origin):
                tab.evaluate('''offset=>{window.csrfOffset=offset;
                    if(!window.originalNow){window.originalNow=Date.now;Date.now=()=>window.originalNow()+window.csrfOffset;}
                }''',state['seconds']*1000)
    with patch('app.services.csrf.time',SimpleNamespace(time=lambda:time.time()+state['seconds'])),patch.object(fixtures.MobileDrawerBrowserTest,'ready',ready):
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
