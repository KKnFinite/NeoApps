"""Event-owned FOB; presentation changes never alter operational quantities."""
from io import BytesIO
import unittest
from unittest.mock import patch

from pypdf import PdfReader
from sqlalchemy import event as sql_event
from app.extensions import db
from app.models import NeoScorpionFuelingEventTankSnapshot, NeoScorpionTailFuelState
from app.services import neoscorpion as fuel
from app.services.neoscorpion_fuel_report_pdf import fuel_report_pdf
from tests.test_neoscorpion_sort_fuel_totals import SortFuelTotalsIntegrationTest


class FuelReportFobTest(unittest.TestCase):
    setUp = SortFuelTotalsIntegrationTest.setUp
    _event = SortFuelTotalsIntegrationTest._event

    def event(self, assignment, sequence, readings, kind='fuel'):
        event = self._event(assignment, sequence, 500, cycle=sequence, kind=kind)
        event.required_fuel_lbs = 40_000
        event.neo_fuel_lbs = 40_100
        if readings is not None:
            event.tank_snapshots = [NeoScorpionFuelingEventTankSnapshot(
                tank_code=code, remaining_lbs=value, actual_lbs=20_000)
                for code, value in readings.items()]
        return event

    def test_event_snapshot_fob_across_visits_uplift_defuel_unknown_and_zero(self):
        mission = self.fixture._mission('UPS901', 'N491UP', 40_000, 1)
        assignment = self.fixture._assignment(mission, self.truck, transfer=500)
        readings = [dict(left=6000, ctr=0, right=6300),
                    dict(left=20_000, ctr=0, right=20_000),
                    dict(left=21_000, ctr=0, right=21_000),
                    None, dict(left=6000, right=6000),
                    dict(left=0, ctr=0, right=0)]
        for sequence, values in enumerate(readings, 1):
            self.event(assignment, sequence, values,
                       {2:'uplift', 3:'defuel'}.get(sequence, 'fuel'))
        # Later shared-tail and mutable work readings are never report sources.
        self.fixture._tail_fuel(mission, inbound_lbs=99_000).fob_lbs = 99_000
        db.session.commit()
        report = fuel.fuel_report_context(self.gateway)
        expected = ['12.3','40.0','42.0','-','-','0.0']
        self.assertEqual([r['fob_display'] for r in report['fuel_report_rows']], expected)
        self.assertEqual(report['sort_fuel_totals']['transfer']['value'], 3000)
        NeoScorpionTailFuelState.query.one().fob_lbs = 123_000
        db.session.commit()
        self.assertEqual(fuel.fuel_report_context(self.gateway), report)
        self.fixture._login_user('report-reader', 'simulator')
        html = self.fixture.client.get('/neoscorpion/reports/fuel').get_data(as_text=True)
        self.assertIn('Fuel on Board, K lbs">FOB</th>', html)
        for value in expected:
            self.assertIn('<td>'+value+'</td>', html)
        response = self.fixture.client.get('/neoscorpion/reports/fuel?format=pdf')
        self.assertEqual(response.status_code, 200)
        pdf = PdfReader(BytesIO(response.data))
        text = ''.join(page.extract_text() for page in pdf.pages)
        self.assertIn('FOB', text)
        for value in expected[:3]:self.assertIn(value, text)
        self.assertNotIn('123.0', text)
        self.assertEqual(self.truck.truck_number, '20')

    def test_unsupported_tail_layout_never_creates_an_estimated_fob(self):
        mission = self.fixture._mission('UPS902', 'N491UP', 40_000, 1)
        assignment = self.fixture._assignment(mission, self.truck, transfer=500)
        self.event(assignment, 1, dict(left=1000, ctr=0, right=1000))
        db.session.commit()
        with patch.object(fuel, 'tank_layout_for_tail', return_value=()):
            self.assertEqual(fuel.fuel_report_context(self.gateway)['fuel_report_rows'][0]['fob_display'], '-')

    def test_event_snapshot_reads_remain_batched_as_report_grows(self):
        mission = self.fixture._mission('UPS902', 'N491UP', 40_000, 1)
        assignment = self.fixture._assignment(mission, self.truck, transfer=500)
        def reads():
            db.session.commit();db.session.expire_all()
            statements=[]
            def capture(_conn,_cursor,statement,*_args):
                if statement.lstrip().upper().startswith('SELECT'):statements.append(statement)
            sql_event.listen(db.engine,'before_cursor_execute',capture)
            try:fuel.fuel_report_context(self.gateway)
            finally:sql_event.remove(db.engine,'before_cursor_execute',capture)
            return statements
        self.event(assignment,1,dict(left=1000,ctr=0,right=1000))
        first=reads()
        for seq in range(2,13):self.event(assignment,seq,dict(left=1000,ctr=0,right=1000))
        many=reads()
        self.assertEqual(len(first),len(many))
        self.assertEqual(sum('FROM neoscorpion_fueling_event_tank_snapshots' in s for s in many),1)

    def test_pdf_long_names_and_many_events_fit_printable_page(self):
        mission = self.fixture._mission('UPS902', 'N491UP', 40_000, 1)
        assignment = self.fixture._assignment(mission, self.truck, transfer=500)
        for seq in range(1, 51):self.event(assignment, seq, dict(left=1000, ctr=0, right=1000))
        db.session.commit()
        report = fuel.fuel_report_context(self.gateway)
        for row in report['fuel_report_rows']:
            row['fueler_name'] = 'A Very Long Fueler Name With Multiple Components'
            row['flight_number'] = 'UPS123456'
            row['truck_number'] = 'TRUCK-1234567890'
        pdf = PdfReader(fuel_report_pdf(report))
        self.assertGreater(len(pdf.pages), 1)
        for page in pdf.pages:
            self.assertIn('FOB', page.extract_text())
            positions = []
            page.extract_text(visitor_text=lambda text, cm, tm, font, size:
                positions.append(cm[4]+tm[4]) if text.strip() else None)
            self.assertTrue(all(14 <= x < 778 for x in positions), positions)
