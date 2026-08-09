import unittest
from unittest.mock import Mock

from app.services.google_motherbrain_reset import (
    INBOUND_RESET_RANGES,
    OUTBOUND_RESET_RANGES,
    build_google_motherbrain_reset_plan,
    dry_run_google_motherbrain_reset,
    execute_google_motherbrain_reset_plan,
)
from app.services.google_motherbrain_sheets import (
    GOOGLE_MOTHERBRAIN_LOCKED_SPREADSHEET_ID,
    GOOGLE_MOTHERBRAIN_RESET_PARKING_FORMULA_RANGE,
)


class GoogleMotherBrainResetPlanTest(unittest.TestCase):
    def test_plan_contains_the_exact_inbound_and_outbound_reset_ranges(self):
        plan = build_google_motherbrain_reset_plan([["=U13"], ["=AC13"]])

        self.assertEqual(
            plan["inbound_ranges"],
            (
                "Inbound!A4:G13",
                "Inbound!A16:G100",
                "Inbound!P4:P100",
            ),
        )
        self.assertEqual(
            plan["outbound_ranges"],
            (
                "Outbound!A4:G13",
                "Outbound!A16:G100",
                "Outbound!P4:P100",
                "Outbound!Y4:Y100",
            ),
        )
        self.assertEqual(plan["spreadsheet_id"], GOOGLE_MOTHERBRAIN_LOCKED_SPREADSHEET_ID)
        self.assertEqual(
            plan["parking_formula_range"],
            GOOGLE_MOTHERBRAIN_RESET_PARKING_FORMULA_RANGE,
        )

    def test_plan_preserves_the_permanent_alp_header_row_15(self):
        plan = build_google_motherbrain_reset_plan([['=U13']])

        self.assertIn("Inbound!A16:G100", plan["clear_ranges"])
        self.assertIn("Outbound!A16:G100", plan["clear_ranges"])
        self.assertFalse(
            any(
                clear_range.startswith(("Inbound!A15:", "Outbound!A15:"))
                for clear_range in plan["clear_ranges"]
            )
        )

    def test_primary_and_secondary_b_parking_cells_are_included_from_formulas(self):
        plan = build_google_motherbrain_reset_plan([["=U13"], ["=AC13"]])

        self.assertEqual(plan["parking_cells"], ("U13", "AC13"))
        self.assertIn("Parking Plan!U13", plan["clear_ranges"])
        self.assertIn("Parking Plan!AC13", plan["clear_ranges"])

    def test_malformed_non_cell_and_layout_formulas_are_ignored(self):
        plan = build_google_motherbrain_reset_plan(
            [
                ["=U13"],
                ["=SUM(U13:U14)"],
                ["=Parking Plan!U13"],
                ["=U13:U14"],
                ["=U3"],
                ["=BG3"],
                ["=BK100"],
                [""],
            ]
        )

        self.assertEqual(plan["parking_cells"], ("U13",))
        self.assertNotIn("Parking Plan!BG3", plan["clear_ranges"])
        self.assertNotIn("Parking Plan!BK100", plan["clear_ranges"])
        self.assertFalse(any("BG" in value for value in plan["clear_ranges"]))

    def test_duplicate_formula_references_are_removed_in_first_seen_order(self):
        plan = build_google_motherbrain_reset_plan(
            [["=U13"], ["=$U$13"], ["=AC13"], ["=U13"]]
        )

        self.assertEqual(plan["parking_cells"], ("U13", "AC13"))

    def test_dry_run_reads_formulas_but_never_invokes_a_writer(self):
        formula_reader = Mock(return_value=[["=U13"], ["=AC13"]])
        writer = Mock()

        plan = dry_run_google_motherbrain_reset(formula_reader=formula_reader)

        formula_reader.assert_called_once_with()
        writer.assert_not_called()
        self.assertEqual(plan["parking_cells"], ("U13", "AC13"))

    def test_execution_is_explicit_and_receives_only_validated_ranges(self):
        plan = build_google_motherbrain_reset_plan([["=U13"]])
        writer = Mock()

        clear_ranges = execute_google_motherbrain_reset_plan(plan, writer=writer)

        self.assertEqual(clear_ranges, plan["clear_ranges"])
        writer.assert_called_once_with(plan["clear_ranges"])

    def test_execution_rejects_ranges_outside_the_reset_plan(self):
        plan = build_google_motherbrain_reset_plan([["=U13"]])
        plan["clear_ranges"] = (*INBOUND_RESET_RANGES, "Parking Plan!A1")

        with self.assertRaisesRegex(ValueError, "unexpected ranges"):
            execute_google_motherbrain_reset_plan(plan, writer=Mock())

    def test_execution_rejects_a_parking_helper_cell(self):
        plan = build_google_motherbrain_reset_plan([["=U13"]])
        plan["parking_cells"] = ("BG3",)

        with self.assertRaisesRegex(ValueError, "unsafe parking cells"):
            execute_google_motherbrain_reset_plan(plan, writer=Mock())


if __name__ == "__main__":
    unittest.main()
