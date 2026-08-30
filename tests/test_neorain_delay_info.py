import unittest
from datetime import date

from app import create_app
from app.extensions import db
from app.models import Gateway, SortDateMission, SortDateOperation
from app.services.neorain_delay_info import (
    NeoRainDelayInfoError, add_neorain_delay_info, delete_neorain_delay_info,
    neorain_delay_info_rows, update_neorain_delay_info,
)


class NeoRainDelayInfoTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app(type("TestConfig", (), {"SECRET_KEY": "test", "TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "SQLALCHEMY_TRACK_MODIFICATIONS": False}))
        self.context = self.app.app_context(); self.context.push(); db.create_all()
        gateway = Gateway(code="RFD", name="RFD"); db.session.add(gateway); db.session.flush()
        operation = SortDateOperation(sort_date=date(2026, 8, 30), gateway_id=gateway.id, gateway_code="RFD", sort_name="night")
        db.session.add(operation); db.session.flush()
        self.first = SortDateMission(sort_date=date(2026, 8, 30), gateway_code="RFD", sort_name="night", sort_date_operation_id=operation.id, mission_type="departure", flight_number="F1", origin="RFD", destination="ONT")
        self.second = SortDateMission(sort_date=date(2026, 8, 30), gateway_code="RFD", sort_name="night", sort_date_operation_id=operation.id, mission_type="departure", flight_number="F2", origin="RFD", destination="ONT")
        db.session.add_all([self.first, self.second]); db.session.commit()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.context.pop()

    def test_multiple_rows_normalize_and_update_delete(self):
        first = add_neorain_delay_info(self.first, "12", " ab ", "  first\nline  ")
        second = add_neorain_delay_info(self.first, 7, "cd", None)
        db.session.commit()
        self.assertEqual([row.code for row in neorain_delay_info_rows(self.first)], ["AB", "CD"])
        self.assertEqual(first.notes, "first\nline")
        update_neorain_delay_info(self.first, first, 15, "ef", "\n revised \n")
        self.assertEqual((first.minutes, first.code, first.notes), (15, "EF", "revised"))
        delete_neorain_delay_info(self.first, second); db.session.commit()
        self.assertEqual(len(neorain_delay_info_rows(self.first)), 1)

    def test_validation_cross_mission_and_no_sum_enforcement(self):
        for minutes in (0, -1, "1.5", "", True):
            with self.subTest(minutes=minutes):
                with self.assertRaises(NeoRainDelayInfoError): add_neorain_delay_info(self.first, minutes, "AA")
        for code in ("", "A", "ABC"):
            with self.subTest(code=code):
                with self.assertRaises(NeoRainDelayInfoError): add_neorain_delay_info(self.first, 1, code)
        row = add_neorain_delay_info(self.first, 999, "AA")
        with self.assertRaises(NeoRainDelayInfoError): update_neorain_delay_info(self.second, row, 1, "BB")
        with self.assertRaises(NeoRainDelayInfoError): delete_neorain_delay_info(self.second, row)

