"""Retained node scope under real PostgreSQL transactions and transfers."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Event
from app.extensions import db
from app.models import User
from app.services import neostaffing as staffing
from app.services import neostaffing_timecards as tc
from tests import test_motherbrain_block_out_authority_postgres as pg
from tests import test_neostaffing_retained_scope as contracts
from tests import test_neostaffing_timecards as fixture


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class RetainedScopePostgresTest(contracts.RetainedScopeTest):
    def setUp(self):
        pg.postgres_setup(self,fixture.fixtures.sektor_fixture,super().setUp)

    def test_edit_waiting_for_transfer_rechecks_current_home_and_rolls_back(self):
        row=self.attendance(self.peer);command=self.command(row)
        uid=self.user.id
        db.session.commit();before=contracts.snapshot();entered=Event()
        # assign_work_area and the edit share the existing employee-parent lock.
        staffing.assign_work_area(self.peer,self.areas['wbm']);db.session.flush()
        def edit():
            with self.app.test_request_context('/neosektor/manage-employees?area=ebm'):
                actor=db.session.get(User,uid);entered.set()
                try:
                    tc.save_segments(actor,[command],as_of=date(2026,9,12),node_workspace='sektor',node_area='ebm')
                    db.session.commit();return 'saved'
                except ValueError as error:
                    db.session.rollback();return str(error)
                finally:db.session.remove()
        with ThreadPoolExecutor(1) as pool:
            job=pool.submit(edit)
            self.assertTrue(entered.wait(10));db.session.commit()
            self.assertIn('outside your leadership scope',job.result(timeout=20))
        db.session.remove();self.assertEqual(contracts.snapshot(),before)
        with self.app.test_request_context('/neosektor/manage-employees?area=wbm'):
            actor=db.session.get(User,uid)
            tc.save_segments(actor,[command],as_of=date(2026,9,12),node_workspace='sektor',node_area='wbm');db.session.commit()
        self.assertEqual(len(tc.read_rows(db.session.get(User,uid),date(2026,9,12),date(2026,9,12))),1)
