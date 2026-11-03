"""The six R03C contracts using the same fixtures on disposable PostgreSQL."""
import unittest
from app.extensions import db

from tests import test_neoscorpion_cancel_unassign as dispatch
from tests import test_neoscorpion_dispatch_planning as planning
from tests import test_neoscorpion_fueling_status as lineage
from tests import test_neoscorpion_outstanding_work as visits
from tests import test_motherbrain_block_out_authority_postgres as pg


@unittest.skipUnless(pg.URI, 'Requires disposable loopback PostgreSQL')
class R03CPostgresTest(unittest.TestCase):
    def run_case(self, case_class, method, module):
        case=case_class(method)
        def exercise():
            self.addCleanup(case.doCleanups)
            case.setUp()
            # Fixture-backed cases register their own teardown; the older
            # direct fixture still owns an explicit tearDown method.
            if case_class is dispatch.CancelUnassignTest:
                self.addCleanup(case.tearDown)
            getattr(case,method)()
            self.assertEqual(db.engine.dialect.name,'postgresql')
        # Keep the factory patch active for lineage's inner canonical fixture.
        pg.postgres_setup(self,module,exercise)

    def test_busy_preparation(self):
        self.run_case(visits.OutstandingWorkTest,'test_busy_truck_preparation_is_not_original_aircraft',planning)

    def test_retained_preparation(self):
        self.run_case(visits.OutstandingWorkTest,'test_retained_same_tail_preparation_remains_reviewable_not_original',planning)

    def test_earlier_physical_visits(self):
        self.run_case(visits.OutstandingWorkTest,'test_genuine_earlier_tail_and_same_tail_physical_visits_keep_recovery',planning)

    def test_trusted_lineage(self):
        # The case constructs and cleans a canonical completed source itself.
        self.run_case(lineage.TrustedFobInheritanceTest,'test_explicit_trusted_lineage_uses_apu_adjusted_fuel_and_preserves_accounting',planning)

    def test_dispatch_cells(self):
        self.run_case(dispatch.CancelUnassignTest,'test_dispatch_visual_contract_reserves_fob_slot_through_live_refresh',dispatch.fixture)

    def test_alert_threshold(self):
        self.run_case(dispatch.CancelUnassignTest,'test_ready_assignment_alert_threshold_refresh',dispatch.fixture)
