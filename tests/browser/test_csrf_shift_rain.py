"""Real Rain MASTER CRUD after the original page token has aged three hours."""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from flask import request
from tests.browser import test_neorain_delay_entry as fixtures


class RainShiftCsrfBrowserTest(unittest.TestCase):
    open_panel=fixtures.RainDelayEntryBrowserTest.open_panel
    add_form=fixtures.RainDelayEntryBrowserTest.add_form
    exercise=fixtures.RainDelayEntryBrowserTest.exercise

    @classmethod
    def setUpClass(cls):
        fixtures.RainDelayEntryBrowserTest.setUpClass.__func__(cls)
        cls.offset=0;cls.renewal_count=0
        cls.clock=patch('app.services.csrf.time',SimpleNamespace(time=lambda:time.time()+cls.offset));cls.clock.start()
        original=cls.fixture.ready
        def aged_ready(instance,page,path):
            original(instance,page,path)
            cls.offset+=3*3600
            page.evaluate('''offset=>{window.csrfOffset=offset;
                if(!window.originalNow){window.originalNow=Date.now;Date.now=()=>window.originalNow()+window.csrfOffset;}
            }''',cls.offset*1000)
        cls.ready_patch=patch.object(cls.fixture,'ready',aged_ready);cls.ready_patch.start()
        @cls.fixture.app.before_request
        def observe():
            if request.endpoint=='auth.renew_csrf':cls.renewal_count+=1

    @classmethod
    def tearDownClass(cls):
        cls.ready_patch.stop();cls.clock.stop()
        fixtures.RainDelayEntryBrowserTest.tearDownClass.__func__(cls)

    def test_chromium_aged_master_inbound_outbound_actual_crud(self):
        before=self.renewal_count;self.exercise('chromium',1920)
        self.assertGreaterEqual(self.renewal_count-before,2)

    def test_webkit_aged_master_inbound_outbound_actual_crud(self):
        before=self.renewal_count;self.exercise('webkit',390)
        self.assertGreaterEqual(self.renewal_count-before,2)
