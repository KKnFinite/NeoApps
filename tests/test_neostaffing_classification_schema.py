import unittest
from datetime import date
from unittest.mock import Mock, patch

from app import create_app
from app.extensions import db
from app.models import StaffingPerson
from app.models.staffing_person import (
    STAFFING_CLASSIFICATIONS,
    STAFFING_DATABASE_CLASSIFICATIONS,
    STAFFING_PHASE1_CLASSIFICATIONS,
)
from app.services import neostaffing as staffing_service
from app.services import neostaffing_bulk_change as bulk_change_service
from app.services import neostaffing_change_requests as change_request_service
from app.services.neostaffing_classification_schema import (
    NEOSTAFFING_CLASSIFICATION_CONSTRAINT,
    NEOSTAFFING_CLASSIFICATION_SCHEMA_LOCK_KEY,
    NEOSTAFFING_CLASSIFICATION_TRANSITION_CONSTRAINT,
    ensure_neostaffing_classification_constraint,
)


class NeoStaffingClassificationSchemaTest(unittest.TestCase):
    def setUp(self):
        self.config = type(
            "NeoStaffingClassificationSchemaConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(self.config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    @staticmethod
    def _person(employee_id, classification):
        return StaffingPerson(
            employee_id=employee_id,
            first_name="Classification",
            last_name="Fixture",
            seniority_date=date(2020, 1, 1),
            classification=classification,
            employee_status="active",
            active=True,
        )

    def test_sqlite_model_constraint_accepts_old_and_phase1_classifications(self):
        people = [
            self._person(f"CLASS-{index}", classification)
            for index, classification in enumerate(
                STAFFING_DATABASE_CLASSIFICATIONS,
                start=1,
            )
        ]
        db.session.add_all(people)
        db.session.commit()

        self.assertEqual(
            {person.classification for person in StaffingPerson.query.all()},
            set(STAFFING_DATABASE_CLASSIFICATIONS),
        )
        self.assertTrue(set(STAFFING_CLASSIFICATIONS).issubset(
            STAFFING_DATABASE_CLASSIFICATIONS
        ))

    def test_phase1_labels_and_semantic_helpers_cover_new_classifications(self):
        expected_labels = {
            "seasonal": "Seasonal",
            "domiciled_full_time_combo": "Domiciled FT Combo",
            "non_domiciled_full_time_combo": "Non-Domiciled FT Combo",
        }
        for classification, label in expected_labels.items():
            with self.subTest(classification=classification):
                self.assertEqual(
                    staffing_service.CLASSIFICATION_LABELS[classification],
                    label,
                )

        self.assertTrue(staffing_service.is_seasonal_classification("seasonal"))
        self.assertEqual(
            staffing_service.union_classification_group("part_time"),
            "part_time",
        )
        for classification in (
            "full_time_combo",
            "domiciled_full_time_combo",
            "non_domiciled_full_time_combo",
        ):
            with self.subTest(classification=classification):
                self.assertEqual(
                    staffing_service.union_classification_group(classification),
                    "full_time",
                )
        self.assertTrue(
            staffing_service.is_domiciled_ft_union_classification(
                "domiciled_full_time_combo"
            )
        )
        self.assertTrue(
            staffing_service.is_non_domiciled_ft_union_classification(
                "non_domiciled_full_time_combo"
            )
        )
        self.assertTrue(staffing_service.is_management_classification("manager"))
        self.assertFalse(staffing_service.is_management_classification("seasonal"))

    def test_phase1_choices_and_mutation_reject_new_classifications(self):
        self.assertEqual(
            [value for value, _label in staffing_service.classification_choices()],
            list(STAFFING_CLASSIFICATIONS),
        )
        self.assertTrue(
            set(STAFFING_PHASE1_CLASSIFICATIONS).isdisjoint(
                value for value, _label in staffing_service.classification_choices()
            )
        )

        for classification in STAFFING_PHASE1_CLASSIFICATIONS:
            values = {
                "employee_id": f"NEW-{classification}",
                "first_name": "New",
                "last_name": "Employee",
                "seniority_date": "2020-01-01",
                "classification": classification,
                "employee_status": "active",
                "active": "1",
            }
            with self.subTest(classification=classification):
                with self.assertRaisesRegex(ValueError, "classification"):
                    staffing_service.create_person(values)
                with self.assertRaisesRegex(ValueError, "classification"):
                    bulk_change_service._normalize_person_field(
                        "classification",
                        classification,
                        None,
                    )
                with self.assertRaisesRegex(
                    ValueError,
                    "Only non-management classification changes are supported",
                ):
                    change_request_service._parse_requested_values(
                        {"requested_classification": classification}
                    )

    def test_seasonal_account_eligibility_rule(self):
        self.assertFalse(
            staffing_service.classification_is_account_eligible("seasonal")
        )
        self.assertFalse(
            staffing_service.classification_is_account_eligible("unsupported")
        )
        for classification in STAFFING_DATABASE_CLASSIFICATIONS:
            if classification == "seasonal":
                continue
            with self.subTest(classification=classification):
                self.assertTrue(
                    staffing_service.classification_is_account_eligible(
                        classification
                    )
                )

    def test_postgresql_constraint_widening_is_targeted_and_idempotent(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()
        full_constraint = "CHECK (classification IN (" + ", ".join(
            f"'{classification}'"
            for classification in STAFFING_DATABASE_CLASSIFICATIONS
        ) + "))"
        connection.execute.return_value.scalar.side_effect = [
            "CHECK (classification IN ('part_time', 'full_time_combo'))",
            full_constraint,
        ]
        with (
            patch(
                "app.services.neostaffing_classification_schema.db.session.connection",
                return_value=connection,
            ),
            patch(
                "app.services.neostaffing_classification_schema.db.session.commit"
            ) as commit,
        ):
            self.assertTrue(ensure_neostaffing_classification_constraint(self.app))
            self.assertTrue(ensure_neostaffing_classification_constraint(self.app))

        statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertIn("SET LOCAL lock_timeout", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertEqual(
            connection.execute.call_args_list[1].args[1]["lock_key"],
            NEOSTAFFING_CLASSIFICATION_SCHEMA_LOCK_KEY,
        )
        self.assertEqual(
            statements.count(
                f"ADD CONSTRAINT {NEOSTAFFING_CLASSIFICATION_TRANSITION_CONSTRAINT}"
            ),
            1,
        )
        self.assertIn("NOT VALID", statements)
        self.assertIn(
            f"VALIDATE CONSTRAINT {NEOSTAFFING_CLASSIFICATION_TRANSITION_CONSTRAINT}",
            statements,
        )
        self.assertIn(
            f"DROP CONSTRAINT IF EXISTS {NEOSTAFFING_CLASSIFICATION_CONSTRAINT}",
            statements,
        )
        self.assertIn(
            f"RENAME CONSTRAINT {NEOSTAFFING_CLASSIFICATION_TRANSITION_CONSTRAINT} "
            f"TO {NEOSTAFFING_CLASSIFICATION_CONSTRAINT}",
            statements,
        )
        self.assertNotIn("DROP TABLE", statements)
        self.assertNotIn("INSERT INTO", statements)
        self.assertNotIn("UPDATE staffing_people", statements)
        self.assertNotIn("DELETE FROM staffing_people", statements)
        self.assertEqual(commit.call_count, 2)

    def test_factory_invokes_targeted_classification_repair(self):
        with patch("app.ensure_neostaffing_classification_constraint") as ensure:
            app = create_app(self.config)
        ensure.assert_called_once_with(app)


if __name__ == "__main__":
    unittest.main()
