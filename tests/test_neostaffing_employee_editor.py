"""Shared profile/flow writes, deployment repair and Seasonal eligibility."""
import unittest
from sqlalchemy import text

from app.extensions import db
from app.models import StaffingPerson, StaffingUnit, StaffingReportingRelationship, User
from app.services import neostaffing as service
from app.services import neostaffing_bulk_change as bulk
from tests import test_neostaffing_people_completion as fixture


class EmployeeEditorTest(unittest.TestCase):
    _user = fixture.PeopleCompletionTest._user
    _grant_app_access = fixture.PeopleCompletionTest._grant_app_access
    _login = fixture.PeopleCompletionTest._login
    _staffing_hierarchy = fixture.PeopleCompletionTest._staffing_hierarchy
    person = fixture.PeopleCompletionTest.person
    setUp = fixture.PeopleCompletionTest.setUp
    tearDown = fixture.PeopleCompletionTest.tearDown

    def employee(self, start=None, classification='part_time', employee_id='EDITOR'):
        person = self.person(employee_id, classification)
        service.assign_work_area(person, start or self.area)
        db.session.commit()
        return person

    def values(self, person, **changes):
        home = service.assignment_service.shift_home(person)
        return dict(employee_id=person.employee_id, first_name=person.first_name,
            last_name=person.last_name, seniority_date='01/01/2000', classification=person.classification,
            employee_status=person.employee_status, active='1', shared_editor='1',
            expected_person_version=service.entity_version(person),
            expected_version=service.shift_flow_revision(person, person.shift_flow_plan, home),
            shift_flow_sort_start_work_area_id=home.work_area_unit_id,
            shift_flow_final_door_work_area_id='', shift_flow_setup_work_area_id='',
            shift_flow_ballmat_transition='', **changes)

    def update(self, person, values):
        return self.client.post(f'/neostaffing/app-management/people/{person.id}/update', data=values)

    def test_same_editor_mount_profile_first_one_save_and_existing_delete_route(self):
        person = self.employee()
        for path in ('/neostaffing/people', '/neostaffing/shift-flow'):
            page = self.client.get(path, query_string={'person_id':person.id}).text
            self.assertIn('data-employee-editor', page)
            self.assertNotIn('SAVE FLOW', page)
        editor = self.client.get('/neostaffing/people/employee-editor', query_string={'person_id':person.id}).text
        self.assertEqual(editor.count('<form '), 1)
        self.assertEqual(editor.count('type="submit"'), 1)
        self.assertLess(editor.index('Employee ID'), editor.index('SHIFT FLOW'))
        self.assertIn(f'/people/{person.id}/delete', editor)
        self.assertIn('csrf_token', editor)
        self.assertIn('Current Work Assignments', editor)

    def test_atomic_save_profile_and_flow_default_and_override(self):
        person = self.employee()
        values = self.values(person); values['first_name'] = 'Updated'
        response = self.update(person, values)
        self.assertEqual(response.status_code, 200, response.text)
        db.session.expire_all()
        self.assertEqual(person.first_name, 'Updated')
        self.assertEqual(person.shift_flow_plan.final_door_work_area_id, self.area.id)
        other = StaffingUnit(unit_type='work_area', name='Door 32', parent=self.department)
        db.session.add(other); db.session.commit()
        values = self.values(person); values['shift_flow_final_door_work_area_id'] = other.id
        self.assertEqual(self.update(person, values).status_code, 200)
        db.session.expire_all()
        self.assertEqual(person.shift_flow_plan.final_door_work_area_id, other.id)

    def test_validation_and_stale_profile_or_flow_roll_back_both(self):
        person = self.employee()
        values = self.values(person); values['first_name'] = 'Never saved'
        values['shift_flow_final_door_work_area_id'] = '999999'
        self.assertEqual(self.update(person, values).status_code, 400)
        db.session.expire_all(); self.assertNotEqual(person.first_name, 'Never Saved')
        self.assertIsNone(person.shift_flow_plan)
        for field in ('expected_version','expected_person_version'):
            values = self.values(person); values[field] = 'stale'; values['first_name'] = 'Never saved'
            self.assertEqual(self.update(person, values).status_code, 400)
            db.session.expire_all(); self.assertIsNone(person.shift_flow_plan)
            self.assertNotEqual(person.first_name, 'Never Saved')
        values = self.values(person); values['seniority_date'] = 'bad'
        self.assertEqual(self.update(person, values).status_code, 400)
        db.session.expire_all(); self.assertIsNone(person.shift_flow_plan)

    def test_shift_creation_requires_start_and_seasonal_receives_work_and_flow(self):
        values = dict(employee_id='NEW-SEASONAL', first_name='New', last_name='Seasonal',
            seniority_date='01/02/2020', classification='seasonal', employee_status='active',
            shared_editor='1', creation_flow='employee', initial_work_area_unit_id=self.area.id,
            require_shift_start='1', shift_flow_sort_start_work_area_id='')
        self.assertEqual(self.client.post('/neostaffing/app-management/people', data=values).status_code, 400)
        self.assertIsNone(StaffingPerson.query.filter_by(employee_id='NEW-SEASONAL').first())
        values['shift_flow_sort_start_work_area_id'] = self.area.id
        response = self.client.post('/neostaffing/app-management/people', data=values)
        self.assertEqual(response.status_code, 200, response.text)
        person = db.session.get(StaffingPerson, response.json['person_id'])
        self.assertEqual(person.classification, 'seasonal')
        self.assertEqual(person.shift_flow_plan.final_door_work_area_id, self.area.id)
        self.assertIn(person.id, [row['person'].id for row in service.shift_flow_context()['rows']])
        self.assertIn('Seasonal', self.client.get('/neostaffing/people?classification=seasonal').text)
        self.assertEqual(bulk._normalize_person_field('classification','Seasonal',None), 'seasonal')
        imported = service.create_people_batch([dict(employee_id='IMPORT-SEASONAL', first_name='Bulk',
            last_name='Person',seniority_date='2020-01-01',classification='Seasonal')], self.area)
        self.assertEqual(imported[0].classification, 'seasonal')

    def test_repair_missing_invalid_plans_idempotent_preserves_valid_and_other_data(self):
        other = StaffingUnit(unit_type='work_area', name='Door 32', parent=self.department)
        ballmat = StaffingUnit(unit_type='work_area', name='West Ballmat', parent=self.department)
        db.session.add_all([other,ballmat]); db.session.commit()
        missing = self.employee(employee_id='MISSING')
        invalid = self.employee(employee_id='INVALID')
        wrong_type = self.employee(employee_id='WRONG-TYPE')
        valid = self.employee(employee_id='VALID')
        inactive = self.employee(employee_id='INACTIVE'); inactive.active = False
        bm = self.employee(ballmat, employee_id='BALLMAT')
        for person, final in ((invalid,self.area),(wrong_type,self.area),(valid,other)):
            service.create_shift_flow_plan(person, dict(shift_flow_sort_start_work_area_id=self.area.id,
                shift_flow_final_door_work_area_id=final.id,shift_flow_setup_work_area_id=other.id),self.area)
        db.session.commit()
        db.session.execute(text('UPDATE staffing_shift_flow_plans SET final_door_work_area_id = NULL WHERE staffing_person_id=:id'), {'id':invalid.id})
        db.session.execute(text('UPDATE staffing_shift_flow_plans SET final_door_work_area_id=:final, ballmat_transition=2 WHERE staffing_person_id=:id'), {'final':ballmat.id, 'id':wrong_type.id})
        db.session.commit(); db.session.expire_all()
        home_ids = {p.id:service.assignment_service.shift_home(p).id for p in (missing,invalid,wrong_type,valid,bm)}
        self.assertEqual(service.repair_shift_door_finals(), 3)
        db.session.commit(); db.session.expire_all()
        self.assertEqual(invalid.shift_flow_plan.setup_work_area_id, other.id)
        self.assertEqual(wrong_type.shift_flow_plan.final_door_work_area_id, self.area.id)
        self.assertEqual(wrong_type.shift_flow_plan.setup_work_area_id, other.id)
        self.assertEqual(wrong_type.shift_flow_plan.ballmat_transition, 2)
        self.assertEqual(valid.shift_flow_plan.final_door_work_area_id,other.id)
        self.assertIsNone(inactive.shift_flow_plan); self.assertIsNone(bm.shift_flow_plan)
        self.assertEqual(service.repair_shift_door_finals(),0)
        self.assertEqual(home_ids, {p.id:service.assignment_service.shift_home(p).id for p in (missing,invalid,wrong_type,valid,bm)})

    def test_needs_all_missing_plans_no_duplicates_and_drop_creates_plan(self):
        ballmat = StaffingUnit(unit_type='work_area',name='West Ballmat',parent=self.department)
        discharge = StaffingUnit(unit_type='work_area',name='Discharge',parent=self.department)
        db.session.add_all([ballmat,discharge]); db.session.commit()
        no_plan = self.employee(ballmat, 'seasonal', 'NO-PLAN')
        incomplete = self.employee(discharge, employee_id='INCOMPLETE')
        service.create_shift_flow_plan(incomplete,dict(shift_flow_sort_start_work_area_id=discharge.id),discharge)
        db.session.commit()
        roster = service.shift_flow_context()['flow_map']['door_roster']
        self.assertEqual({row['person'].id for row in roster['needs_assignment']},{no_plan.id,incomplete.id})
        self.assertEqual(len(roster['needs_assignment']),2)
        home = service.assignment_service.shift_home(no_plan)
        response = self.client.post(f'/neostaffing/shift-flow/{no_plan.id}/final-door',json={
            'final_door_work_area_id':self.area.id, 'expected_version':service.shift_flow_revision(no_plan,None,home)})
        self.assertEqual(response.status_code,200,response.text)
        db.session.expire_all(); self.assertIsNotNone(no_plan.shift_flow_plan)
        roster = service.shift_flow_context()['flow_map']['door_roster']
        self.assertEqual([row['person'].id for row in roster['needs_assignment']],[incomplete.id])

    def test_delete_history_error_and_success_preserve_linked_login(self):
        db.session.add(StaffingReportingRelationship(person=self.primary, reports_to_person=self.secondary, active=False))
        db.session.commit()
        response = self.client.post(f'/neostaffing/app-management/people/{self.primary.id}/delete',
            data={'shared_editor':'1','expected_person_version':service.entity_version(self.primary)})
        self.assertEqual(response.status_code,400)
        self.assertIn('history',response.json['error'])
        person = self.employee()
        user = self._user('linked-login'); user.employee_id = person.employee_id
        db.session.commit(); uid = user.id; pid = person.id
        response = self.client.post(f'/neostaffing/app-management/people/{pid}/delete',
            data={'shared_editor':'1','expected_person_version':service.entity_version(person)})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIsNone(db.session.get(StaffingPerson,pid))
        self.assertIsNotNone(db.session.get(User,uid))

    def test_editor_save_and_delete_enforce_existing_permission(self):
        person = self.employee()
        operator = self._user('editor-viewer'); self._grant_app_access(operator,'neostaffing','watcher')
        db.session.commit(); self._login(operator.username)
        page = self.client.get('/neostaffing/people', query_string={'person_id':person.id})
        self.assertEqual(page.status_code,200)
        self.assertIn(person.full_name, page.text)
        self.assertIn('Current Work Assignments', page.text)
        self.assertNotIn('data-employee-editor', page.text)
        self.assertEqual(self.client.get('/neostaffing/people/employee-editor',query_string={'person_id':person.id}).status_code,302)
        self.assertEqual(self.update(person,self.values(person)).status_code,302)
        self.assertEqual(self.client.post(f'/neostaffing/app-management/people/{person.id}/delete',data={'shared_editor':'1'}).status_code,302)
        self.assertIsNotNone(db.session.get(StaffingPerson,person.id))
