"""Existing employee identity, confirmed transfers and optional Seasonal seniority."""
import unittest
from unittest.mock import patch
from html.parser import HTMLParser
from sqlalchemy import event
from app.extensions import db
from app.models import StaffingPerson, StaffingUnit, StaffingWorkAssignment, User
from app.services import neostaffing as staffing
from tests import test_neostaffing_employee_editor as fixtures


class EmployeeTransferTest(unittest.TestCase):
    setUp = fixtures.EmployeeEditorTest.setUp
    tearDown = fixtures.EmployeeEditorTest.tearDown
    _user = fixtures.EmployeeEditorTest._user
    _grant_app_access = fixtures.EmployeeEditorTest._grant_app_access
    _login = fixtures.EmployeeEditorTest._login
    _staffing_hierarchy = fixtures.EmployeeEditorTest._staffing_hierarchy
    person = fixtures.EmployeeEditorTest.person
    employee = fixtures.EmployeeEditorTest.employee

    def deice(self):
        dept = staffing.create_unit(dict(unit_type='department', name='Aviation Services', parent_id=self.operation.id))
        area = staffing.create_unit(dict(unit_type='work_area', name='Deice', parent_id=dept.id))
        db.session.commit()
        return area

    def transfer_values(self, person, destination):
        source = StaffingWorkAssignment.query.filter_by(person_id=person.id, active=True).one()
        return dict(source_assignment_id=source.id, destination_work_area_id=destination.id,
            expected_person_version=staffing.entity_version(person),
            expected_assignment_version=str(person.shift_flow_version), confirm_transfer='yes')

    def transfer(self, person, values):
        return self.client.post(f'/neostaffing/people/{person.id}/transfer', data=values)

    def test_shared_editor_offers_confirmed_transfer(self):
        person = self.employee()
        page = self.client.get('/neostaffing/people/employee-editor', query_string={'person_id':person.id})
        self.assertEqual(page.status_code, 200)
        self.assertIn('data-employee-transfer', page.text)

    def test_seasonal_can_be_created_without_a_seniority_date(self):
        response = self.client.post('/neostaffing/app-management/people', data=dict(
            employee_id='UNDATED-SEASONAL', first_name='Seasonal', last_name='Employee',
            seniority_date='', classification='seasonal', employee_status='active', active='1',
            shared_editor='1', creation_flow='employee', initial_work_area_unit_id=self.deice().id))
        self.assertEqual(response.status_code, 200, response.text)
        person = db.session.get(StaffingPerson, response.json['person_id'])
        self.assertIsNone(person.seniority_date)
        self.assertIn('UNDATED-SEASONAL', self.client.get('/neostaffing/people').text)

    def test_transfer_preserves_employee_and_assignment_history(self):
        person = self.employee(); destination = self.deice()
        original = StaffingWorkAssignment.query.filter_by(person_id=person.id, active=True).one()
        original_id, employee_id, seniority = person.id, person.employee_id, person.seniority_date
        response = self.transfer(person, self.transfer_values(person,destination))
        self.assertEqual(response.status_code, 200, response.text)
        db.session.expire_all()
        self.assertEqual((person.id,person.employee_id,person.seniority_date),(original_id,employee_id,seniority))
        self.assertFalse(original.active)
        self.assertEqual(original.work_area_unit_id,self.area.id)
        current = StaffingWorkAssignment.query.filter_by(person_id=person.id,active=True).one()
        self.assertEqual(current.work_area_unit_id,destination.id)
        self.assertNotEqual(current.id,original.id)
        self.assertIsNone(person.shift_flow_plan)

    def test_leaving_shift_clears_plan_and_roster_reverse_establishes_selected_start(self):
        person = self.employee(); destination = self.deice()
        staffing.create_shift_flow_plan(person, {'shift_flow_sort_start_work_area_id':self.area.id},self.area)
        db.session.commit()
        self.assertIsNotNone(person.shift_flow_plan)
        self.assertEqual(self.transfer(person,self.transfer_values(person,destination)).status_code,200)
        db.session.expire_all()
        self.assertNotIn(person.id,[r['person'].id for r in staffing.shift_flow_context()['rows']])
        self.assertIsNone(person.shift_flow_plan)
        self.assertEqual(self.transfer(person,self.transfer_values(person,self.area)).status_code,200)
        db.session.expire_all()
        self.assertEqual(person.shift_flow_plan.sort_start_work_area_id,self.area.id)
        self.assertEqual(person.shift_flow_plan.final_door_work_area_id,self.area.id)
        self.assertIn(person.id,[r['person'].id for r in staffing.shift_flow_context()['rows']])
        self.assertEqual(StaffingWorkAssignment.query.filter_by(person_id=person.id,active=True).count(),1)
        self.assertEqual(StaffingWorkAssignment.query.filter_by(person_id=person.id).count(),3)

    def test_confirmation_invalid_destination_and_stale_requests_do_not_mutate(self):
        person = self.employee(); destination = self.deice()
        original = self.transfer_values(person,destination)
        for change,status in [({'confirm_transfer':''},400),({'destination_work_area_id':self.department.id},400),
                              ({'source_assignment_id':'99999'},409),({'expected_person_version':'old'},409),
                              ({'expected_assignment_version':'old'},409)]:
            with self.subTest(change=change):
                response=self.transfer(person,{**original,**change})
                self.assertEqual(response.status_code,status,response.text)
                db.session.expire_all()
                self.assertEqual(StaffingWorkAssignment.query.filter_by(person_id=person.id,active=True).one().work_area_unit_id,self.area.id)
        self.assertEqual(self.transfer(person,original).status_code,200)
        self.assertEqual(self.transfer(person,original).status_code,409)
        self.assertEqual(StaffingWorkAssignment.query.filter_by(person_id=person.id).count(),2)

    def test_same_destination_is_noop_and_get_has_no_writes(self):
        person=self.employee(); version=person.shift_flow_version; updated=person.updated_at
        writes=[]
        def sql(conn,cursor,statement,*args):
            if statement.lower().lstrip().startswith(('insert ','update ','delete ')):writes.append(statement)
        event.listen(db.engine,'before_cursor_execute',sql)
        try:
            self.client.get('/neostaffing/people/employee-editor',query_string={'person_id':person.id})
        finally:event.remove(db.engine,'before_cursor_execute',sql)
        self.assertEqual(writes,[])
        response=self.transfer(person,self.transfer_values(person,self.area))
        self.assertEqual(response.status_code,200,response.text);self.assertFalse(response.json['changed'])
        self.assertEqual((person.shift_flow_version,person.updated_at),(version,updated))
        self.assertEqual(StaffingWorkAssignment.query.filter_by(person_id=person.id).count(),1)

    def test_transfer_failure_rolls_back_source_and_flow(self):
        person=self.employee(); deice=self.deice()
        self.assertEqual(self.transfer(person,self.transfer_values(person,deice)).status_code,200)
        values=self.transfer_values(person,self.area);version=person.shift_flow_version
        with patch.object(staffing,'create_shift_flow_plan',side_effect=ValueError('Synthetic flow failure')):
            self.assertEqual(self.transfer(person,values).status_code,400)
        db.session.expire_all()
        self.assertEqual(person.shift_flow_version,version)
        self.assertEqual(StaffingWorkAssignment.query.filter_by(person_id=person.id,active=True).one().work_area_unit_id,deice.id)
        self.assertEqual(StaffingWorkAssignment.query.filter_by(person_id=person.id).count(),2)

    def test_view_only_user_cannot_transfer_or_open_editor(self):
        person=self.employee();values=self.transfer_values(person,self.deice())
        viewer=self._user('transfer-viewer');self._grant_app_access(viewer,'neostaffing','watcher');db.session.commit();self._login(viewer.username)
        self.assertEqual(self.transfer(person,values).status_code,403)
        self.assertEqual(self.client.get('/neostaffing/people/employee-editor',query_string={'person_id':person.id}).status_code,302)
        self.assertEqual(StaffingWorkAssignment.query.filter_by(person_id=person.id,active=True).one().work_area_unit_id,self.area.id)

    def test_seasonal_date_optional_but_other_classifications_require_valid_date(self):
        person=staffing.create_person(dict(employee_id='NULL-DATE',first_name='Optional',last_name='Date',classification='seasonal'))
        db.session.commit();self.assertIsNone(person.seniority_date)
        values=dict(employee_id=person.employee_id,first_name=person.first_name,last_name=person.last_name,
            seniority_date='',classification='part_time',employee_status='active',active='1',shared_editor='1',
            expected_person_version=staffing.entity_version(person))
        route=f'/neostaffing/app-management/people/{person.id}/update'
        self.assertEqual(self.client.post(route,data=values).status_code,400)
        db.session.expire_all();self.assertEqual(person.classification,'seasonal');self.assertIsNone(person.seniority_date)
        values['seniority_date']='02/03/2020'
        self.assertEqual(self.client.post(route,data=values).status_code,200)
        self.assertEqual(person.seniority_date.isoformat(),'2020-02-03')
        self.assertEqual(person.classification,'part_time')

    def test_combo_transfer_changes_only_the_selected_sort_and_rejects_duplicate_placement(self):
        person=self.employee(classification='full_time_combo');destination=self.deice()
        day=staffing.create_unit(dict(unit_type='sort',name='Day'))
        ramp=staffing.create_unit(dict(unit_type='operation',name='Ramp',parent_id=day.id))
        area=staffing.create_unit(dict(unit_type='work_area',name='Day Work',parent_id=ramp.id))
        day_assignment=staffing.assign_work_area(person,area);db.session.commit()
        source=StaffingWorkAssignment.query.filter_by(person_id=person.id,active=True,work_area_unit_id=self.area.id).one()
        values=dict(source_assignment_id=source.id,destination_work_area_id=area.id,
            expected_person_version=staffing.entity_version(person),expected_assignment_version=str(person.shift_flow_version),confirm_transfer='yes')
        self.assertEqual(self.transfer(person,values).status_code,400)
        values['destination_work_area_id']=destination.id
        self.assertEqual(self.transfer(person,values).status_code,200)
        db.session.expire_all()
        self.assertTrue(day_assignment.active);self.assertEqual(day_assignment.work_area_unit_id,area.id)
        self.assertEqual({r.work_area_unit_id for r in StaffingWorkAssignment.query.filter_by(person_id=person.id,active=True)}, {area.id,destination.id})

    def test_linked_user_and_personal_fields_are_untouched(self):
        person=self.employee();destination=self.deice()
        user=User(username='linked-employee',employee_id=person.employee_id,password_hash='synthetic-no-login',role='watcher',is_active=True)
        db.session.add(user);db.session.commit()
        before_user={c.name:getattr(user,c.name) for c in User.__table__.columns}
        before_person={c.name:getattr(person,c.name) for c in StaffingPerson.__table__.columns if c.name not in {'shift_flow_version','updated_at'}}
        self.assertEqual(self.transfer(person,self.transfer_values(person,destination)).status_code,200)
        db.session.expire_all()
        self.assertEqual({c.name:getattr(user,c.name) for c in User.__table__.columns},before_user)
        self.assertEqual({key:getattr(person,key) for key in before_person},before_person)

    def test_undated_seasonal_displays_sorts_and_snapshots_without_fake_dates(self):
        area=self.deice();regular=self.employee(start=area,employee_id='DATED')
        person=staffing.create_person(dict(employee_id='UNDATED',first_name='No',last_name='Seniority',classification='seasonal'))
        staffing.assign_work_area(person,area);db.session.commit()
        self.assertEqual(self.client.get('/neostaffing/people').status_code,200)
        self.assertEqual(self.client.get('/neostaffing/bulk-change').status_code,200)
        self.assertEqual(self.client.get('/neostaffing/reports',query_string={'report_type':'seniority','operation_id':self.operation.id}).status_code,200)
        rows=staffing.seniority_context({'operation_id':self.operation.id})['rows']
        self.assertLess(next(i for i,r in enumerate(rows) if r['person'].id==regular.id),next(i for i,r in enumerate(rows) if r['person'].id==person.id))
        self.assertIsNone(person.seniority_date)

    def test_transfer_post_requires_csrf_and_revoked_access_cannot_mutate(self):
        person=self.employee();values=self.transfer_values(person,self.deice())
        self.app.config.update(CSRF_ENABLED=True,CSRF_PROTECT_TESTING=True)
        class Meta(HTMLParser):
            token=None
            def handle_starttag(self,tag,attrs):
                attrs=dict(attrs)
                if tag=='meta' and attrs.get('name')=='csrf-token':self.token=attrs['content']
        parser=Meta();parser.feed(self.client.get('/neostaffing/people').text)
        self.assertTrue(parser.token)
        self.assertEqual(self.transfer(person,values).status_code,400)
        self.assertEqual(self.transfer(person,{**values,'csrf_token':parser.token}).status_code,200)
        from app.models import PortalAppAccess
        admin=User.query.filter_by(username='completion-admin').one()
        grant=PortalAppAccess.query.filter_by(user_id=admin.id,app_code='neostaffing').one()
        grant.is_active=False;db.session.commit()
        new_values=self.transfer_values(person,self.area)
        self.assertEqual(self.transfer(person,{**new_values,'csrf_token':parser.token}).status_code,403)
        self.assertEqual(StaffingWorkAssignment.query.filter_by(person_id=person.id,active=True).one().work_area_unit_id,int(values['destination_work_area_id']))
