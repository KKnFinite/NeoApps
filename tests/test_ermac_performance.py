"""NeoErmac read projection, payload equivalence and fresh-request boundaries."""
import json
import re
import unittest
from unittest.mock import patch
from sqlalchemy import event
from app.extensions import db
from app.models import SortDateOperation
from app.services import gateway_matrix, operation_lifecycle
from tests import test_neoermac_read_budgets as fixtures
from tests import test_neosektor_poll_queries as lifecycle_fixtures


class ErmacPerformanceTest(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.NeoErmacReadBudgetTest();self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def test_every_state_keeps_payload_and_never_reads_provider_snapshot(self):
        c=self.case;opid=c.operation_id;original=json.dumps({'history':'x'*16000})
        db.session.get(SortDateOperation,opid).flight_api_last_poll_snapshot_json=original
        db.session.commit()
        for path in ('/neoermac/door-view/state?door=D1&revision=old','/neoermac/building-lineup/state?revision=old',
                     '/neoermac/upcoming-pulls/state?revision=old','/neoermac/view-outbound/state?revision=old'):
            with self.subTest(path=path):
                sql=[]
                def capture(conn,cur,statement,*args):sql.append(statement)
                event.listen(db.engine,'before_cursor_execute',capture)
                try:actual,metrics=c.measure(path)
                finally:event.remove(db.engine,'before_cursor_execute',capture)
                self.assertEqual(metrics['status'],200)
                self.assertEqual([metrics[k] for k in ('INSERT','UPDATE','DELETE','commits')],[0]*4)
                self.assertFalse(any('flight_api_last_poll_snapshot_json' in s for s in sql))
                with patch.object(gateway_matrix,'operation_read_options',return_value=()),patch.object(operation_lifecycle,'operation_read_options',return_value=()):
                    reference,_=c.measure(path)
                def normalized(response):
                    return re.sub(r'(name=\\?"csrf_token\\?"[^>]*value=\\?")[^"\\]*',r'\1TOKEN',json.dumps(response.json,sort_keys=True))
                self.assertEqual(normalized(actual),normalized(reference))
        self.assertEqual(db.session.get(SortDateOperation,opid).flight_api_last_poll_snapshot_json,original)

    def test_configured_lifecycle_parent_is_loaded_with_its_children_once(self):
        from app.services.sort_timeline import ensure_sort_timeline_settings
        from app.models import Gateway
        c=self.case
        ensure_sort_timeline_settings(db.session.get(Gateway,c.gateway_id));db.session.commit()
        for path in ('/neoermac/building-lineup/state?revision=old','/neoermac/upcoming-pulls/state?revision=old'):
            response,metrics=c.measure(path)
            with patch.object(operation_lifecycle,'_sort_settings_for_gateway',side_effect=lifecycle_fixtures.legacy_sort_settings):
                previous,old=c.measure(path)
            self.assertEqual(response.json,previous.json)
            self.assertEqual(metrics['SELECT'],old['SELECT']-1)
