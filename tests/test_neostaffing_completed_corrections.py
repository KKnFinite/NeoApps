"""Current-week corrections use the same attendance and frozen workday identities."""
from datetime import date, datetime
from html import unescape
import re
import unittest

from app.extensions import db
from app.models import StaffingDailyAttendance, StaffingAttendanceOccurrence, StaffingAttendanceSummary
from app.models.staffing_accountability import StaffingAccountabilityResolution as Resolution
from app.services import neostaffing as staffing
from app.services import neostaffing_discipline as discipline
from app.services.neostaffing_attendance_history import finalize_attendance_summaries
from tests import test_neosektor_employees as fixtures


class CompletedCorrectionTest(unittest.TestCase):
    setUp = fixtures.SektorEmployeesTest.setUp
    setUpBase = fixtures.SektorEmployeesTest.setUpBase
    tearDown = fixtures.SektorEmployeesTest.tearDown
    _login_approved_user = fixtures.SektorEmployeesTest._login_approved_user
    _add_sort_operation = fixtures.SektorEmployeesTest._add_sort_operation
    person = fixtures.SektorEmployeesTest.person

    def form(self, status):
        response = self.client.get('/neosektor/manage-employees?area=ebm')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        person_id = self.workers['ebm'].id
        original = re.search(fr'name="original_{person_id}" value="([^"]+)"', html)
        operation = re.search(r'name="sort_date_operation_id" value="([^"]+)"', html)
        return {'sort_date_operation_id': operation[1], f'original_{person_id}': unescape(original[1]),
                f'status_{person_id}': status}

    def save(self, values):
        return self.client.post('/neosektor/manage-employees?area=ebm', data=values,
                                headers={'Accept': 'application/json'})

    def test_completed_correction_restore_preserves_issued_resolution_and_rejects_stale(self):
        operation = self._add_sort_operation(date(2026, 9, 11), 'night')
        self.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE'] = datetime(2026, 9, 11, 23)
        self.assertEqual(self.save(self.form('no_call')).status_code, 200)
        finalize_attendance_summaries(operation, self.user)
        db.session.commit()
        summaries_before = {row.id: (row.finalized_at, row.worked_count)
                            for row in StaffingAttendanceSummary.query.all()}
        self.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE'] = datetime(2026, 9, 12, 12)
        facts = lambda: db.session.execute(discipline.finalized_facts(date(2026, 9, 12),
            [self.workers['ebm'].id])).mappings().all()
        self.assertEqual(len(facts()), 1)
        workday_id = facts()[0]['id']
        issued = Resolution(person_id=self.workers['ebm'].id, kind='issue',
            action='Warning Letter', recommendation='Warning Letter', actor_id=self.user.id,
            resolved_on=date(2026, 9, 12), trigger_workday_id=workday_id)
        db.session.add(issued)
        db.session.commit()
        original_resolution = (issued.id, issued.action, issued.resolved_at, issued.trigger_workday_id)
        stale = self.form('call_in')
        self.assertEqual(self.save(self.form('here')).status_code, 200)
        self.assertTrue(any(row.worked_count > summaries_before[row.id][1]
                            for row in StaffingAttendanceSummary.query.all()))
        self.assertEqual(facts(), [])
        self.assertEqual(self.save(stale).status_code, 409)
        self.assertEqual(StaffingDailyAttendance.query.filter_by(person_id=self.workers['ebm'].id).one().status, 'here')
        self.assertEqual(self.save(self.form('call_in')).status_code, 200)
        self.assertEqual(len(facts()), 1)
        self.assertEqual(facts()[0]['id'], workday_id)
        self.assertEqual(StaffingAttendanceOccurrence.query.filter_by(person_id=self.workers['ebm'].id).count(), 1)
        self.assertEqual(StaffingDailyAttendance.query.filter_by(person_id=self.workers['ebm'].id).count(), 1)
        db.session.expire_all()
        self.assertEqual((issued.id, issued.action, issued.resolved_at, issued.trigger_workday_id), original_resolution)
        self.assertEqual(Resolution.query.count(), 1)
        self.assertEqual({row.id: (row.finalized_at, row.worked_count)
                          for row in StaffingAttendanceSummary.query.all()}, summaries_before)
        self.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE'] = datetime(2026, 9, 13, 12)
        self.assertEqual(self.save(stale).status_code, 409)
