"""Read-only DUCC ETD binding follows the existing Operations display source."""
import unittest
from datetime import datetime
from sqlalchemy import event
from app.extensions import db
from app.services.neosubzero_ucc import neosubzero_ucc_context
from app.services.neosubzero_operations import operations_context
from tests import test_neosubzero_ucc as fixtures


class DuccDisplayTest(unittest.TestCase):
    def test_etd_matches_operations_without_writes_or_schedule_changes(self):
        fixture = fixtures.NeoSubZeroUccTest()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.alpha.eta_datetime_utc = datetime(2026,10,23,4,37)
        # Initialize ordinary fixture settings before measuring these reads.
        neosubzero_ucc_context(fixture.gateway,fixture.operation)
        db.session.commit()
        before = (fixture.alpha.planned_datetime_utc,fixture.alpha.eta_datetime_utc,fixture.alpha.assigned_tail_number)
        writes = []
        def record(_connection,_cursor,statement,_parameters,_context,_many):
            if statement.lstrip().split(None,1)[0].upper() in {'INSERT','UPDATE','DELETE'}:
                writes.append(statement)
        event.listen(db.engine,'before_cursor_execute',record)
        try:
            context = neosubzero_ucc_context(fixture.gateway,fixture.operation)
            ducc = next(row for ramp in context['ramps'] for row in ramp['aircraft'] if row['mission_id']==fixture.alpha.id)
            operations = next(row for row in operations_context(fixture.gateway,fixture.operation,'outbound')['rows'] if row['mission_id']==fixture.alpha.id)
            self.assertEqual(ducc['etd'],operations['etd'])
            self.assertNotEqual(ducc['etd'],'—')
            self.assertEqual(before,(fixture.alpha.planned_datetime_utc,fixture.alpha.eta_datetime_utc,fixture.alpha.assigned_tail_number))
            self.assertEqual(writes,[])
        finally:
            event.remove(db.engine,'before_cursor_execute',record)
