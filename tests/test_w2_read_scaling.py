"""Synthetic read scaling; population growth must not add per-row SELECTs."""
from datetime import date
import unittest
from app.extensions import db
from app.models import StaffingPerson,StaffingWorkAssignment,StaffingDailyAttendance,SortDateOperation
from tests import test_motherbrain_live_collaboration as motherbrain
from tests import test_neoermac_read_budgets as ermac


class ReadScalingTest(unittest.TestCase):
    def test_motherbrain_one_fifty_missions_changed_and_unchanged_are_constant(self):
        f=motherbrain.MotherBrainLiveCollaborationTest();f.setUp()
        try:
            measured=[]
            for size in (1,50):
                for i in range(0 if size==1 else 1,size):
                    db.session.add(f._mission('departure',f'UPS{9000+i}',assigned_tail_number=f'N{900+i}UP'))
                db.session.commit();path=f._state_url('departure',revision='old')
                f.client.get(path)
                with f._capture_sql() as changed:response=f.client.get(path)
                self.assertEqual(response.status_code,200);self.assertTrue(response.json['changed'])
                with f._capture_sql() as same:unchanged=f.client.get(f._state_url('departure',revision=response.json['revision']))
                self.assertFalse(unchanged.json['changed']);self.assertNotIn('fragments',unchanged.json)
                for capture in (changed,same):
                    self.assertEqual(capture['writes'],[]);self.assertEqual(capture['commits'],0)
                measured.append((len(changed['selects'])+1,len(same['selects'])+1))
            self.assertEqual(measured[0],measured[1]);print('W2_MOTHERBRAIN_SCALE',measured)
        finally:f.tearDown()

    def test_ermac_one_hundred_extra_authorized_employees_keep_query_count(self):
        f=ermac.NeoErmacReadBudgetTest();f.setUp()
        try:
            before,small=f.measure('/neoermac/door-view/manage-employees')
            operation=db.session.get(SortDateOperation,f.operation_id)
            sort_id=StaffingDailyAttendance.query.first().sort_unit_id
            for i in range(100):
                person=StaffingPerson(employee_id=f'W2-{i}',first_name='Synthetic',last_name=f'Worker {i}',
                    seniority_date=date(2020,1,1),classification='part_time',employee_status='active',active=True)
                db.session.add(person);db.session.flush()
                db.session.add(StaffingWorkAssignment(person_id=person.id,work_area_unit_id=f.door_id))
                db.session.add(StaffingDailyAttendance(person_id=person.id,sort_unit_id=sort_id,
                    sort_date_operation_id=operation.id,attendance_date=operation.sort_date,work_area_unit_id=f.door_id,status='here'))
            db.session.commit()
            after,large=f.measure('/neoermac/door-view/manage-employees')
            self.assertEqual((before.status_code,after.status_code),(200,200))
            self.assertEqual(small['SELECT'],large['SELECT'])
            self.assertGreater(large['hydrated']['StaffingPerson'],small['hydrated']['StaffingPerson'])
            for sample in (small,large):
                self.assertEqual([sample[k] for k in ('INSERT','UPDATE','DELETE','commits')],[0]*4)
            print('W2_ERMAC_SCALE',small,large)
        finally:f.tearDown()
