from datetime import date
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import SortDateOperation
from app.services.load_planning_contact import (
    current_load_planning_contact,
    set_load_planning_contact,
)


class LoadPlanningContactTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "LoadPlanningContactTestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_explicit_current_values_win(self):
        operation = self._operation(
            date(2026, 9, 2), extension="124", radio_channel="RAMP-2"
        )
        self._operation(date(2026, 9, 1), extension="999", radio_channel="OLD")
        db.session.commit()

        self.assertEqual(
            current_load_planning_contact(operation),
            {"extension": "124", "radio_channel": "RAMP-2"},
        )

    def test_null_current_values_carry_forward_per_field_from_prior_sort(self):
        self._operation(date(2026, 9, 1), extension="123", radio_channel="A-1")
        current = self._operation(date(2026, 9, 2))
        db.session.commit()

        self.assertEqual(
            current_load_planning_contact(current),
            {"extension": "123", "radio_channel": "A-1"},
        )
        self.assertIsNone(current.load_planner_extension)
        self.assertIsNone(current.load_planner_radio_channel)

    def test_explicit_blank_blocks_inheritance_without_affecting_other_field(self):
        self._operation(date(2026, 9, 1), extension="123", radio_channel="A-1")
        current = self._operation(date(2026, 9, 2), extension="", radio_channel=None)
        db.session.commit()

        self.assertEqual(
            current_load_planning_contact(current),
            {"extension": "", "radio_channel": "A-1"},
        )

    def test_carry_forward_is_scoped_to_gateway_and_sort(self):
        self._operation(date(2026, 9, 1), extension="NIGHT", radio_channel="N1")
        self._operation(
            date(2026, 9, 3),
            gateway_code="OTHER",
            extension="OTHER",
            radio_channel="O1",
        )
        self._operation(
            date(2026, 9, 4),
            sort_name="day",
            extension="DAY",
            radio_channel="D1",
        )
        current = self._operation(date(2026, 9, 5))
        db.session.commit()

        self.assertEqual(
            current_load_planning_contact(current),
            {"extension": "NIGHT", "radio_channel": "N1"},
        )
        self.assertEqual(
            current_load_planning_contact(None),
            {"extension": "", "radio_channel": ""},
        )

    def test_setter_stages_both_values_without_committing(self):
        operation = self._operation(date(2026, 9, 1))
        with patch("app.services.load_planning_contact.db.session.commit") as commit:
            result = set_load_planning_contact(
                operation,
                extension=" EXT 12 ",
                radio_channel=" 7A ",
            )

        self.assertIs(result, operation)
        self.assertEqual(operation.load_planner_extension, "EXT 12")
        self.assertEqual(operation.load_planner_radio_channel, "7A")
        commit.assert_not_called()

    def _operation(
        self,
        sort_date,
        *,
        gateway_code="RFD",
        sort_name="night",
        extension=None,
        radio_channel=None,
    ):
        operation = SortDateOperation(
            sort_date=sort_date,
            gateway_code=gateway_code,
            sort_name=sort_name,
            load_planner_extension=extension,
            load_planner_radio_channel=radio_channel,
        )
        db.session.add(operation)
        db.session.flush()
        return operation


if __name__ == "__main__":
    unittest.main()
