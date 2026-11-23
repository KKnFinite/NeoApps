"""Exercise management vacation turn locking on disposable PostgreSQL."""
import unittest

from tests import test_neostaffing_management_vacation_picks as picks
from tests.test_sektor_ermac_performance_postgres import URI, postgres


@unittest.skipUnless(URI, "Requires disposable loopback PostgreSQL")
class ManagementTurnPostgresTest(unittest.TestCase):
    def test_pick_order_pass_and_existing_turn(self):
        case = picks.NeoStaffingManagementVacationPicksTest(
            "test_initial_turn_seniority_junior_block_pass_and_later_pick"
        )
        postgres(self, picks, case.setUp)
        self.addCleanup(case.tearDown)
        case.test_initial_turn_seniority_junior_block_pass_and_later_pick()
