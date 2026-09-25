"""Single-person creation and canonical management scope regression coverage."""
import re
import unittest
from urllib.parse import urlparse, parse_qs

from app.extensions import db
from app.models import StaffingPerson, StaffingLeadershipAssignment, StaffingTwentyCAffiliation, StaffingReportingRelationship, StaffingUnit
from app.services import neostaffing as service
from tests import test_neostaffing_routes as fixtures


class PeopleCompletionTest(unittest.TestCase):
    _user = fixtures.NeoStaffingRoutesTest._user
    _grant_app_access = fixtures.NeoStaffingRoutesTest._grant_app_access
    _login = fixtures.NeoStaffingRoutesTest._login
    _staffing_hierarchy = fixtures.NeoStaffingRoutesTest._staffing_hierarchy
    tearDown = fixtures.NeoStaffingRoutesTest.tearDown

    def setUp(self):
        fixtures.NeoStaffingRoutesTest.setUp(self)
        admin = self._user('completion-admin')
        self._grant_app_access(admin, 'neostaffing', 'grandmaster')
        self.sort, self.operation, self.department, self.area = self._staffing_hierarchy()
        self.sort.name, self.operation.name, self.department.name, self.area.name = 'Night', 'Ramp', 'Shift', 'Door 34'
        self.primary = self.person('PRIMARY')
        self.secondary = self.person('SECONDARY')
        for person in (self.primary, self.secondary):
            db.session.add(StaffingLeadershipAssignment(person=person, unit=self.department, leadership_level='department'))
        db.session.commit()
        self._login(admin.username)

    def person(self, employee_id, classification='full_time_supervisor'):
        return service.create_person(dict(employee_id=employee_id, first_name=employee_id,
            last_name='Supervisor', seniority_date='2000-01-01', classification=classification))

    def post(self, **overrides):
        data = dict(employee_id='NEW', first_name='New', last_name='Person', seniority_date='2001-02-03',
                    phone_number='8155551234', classification='twenty_c_full_time_supervisor',
                    employee_status='active', creation_flow='management',
                    twenty_c_primary=f'{self.sort.id}:{self.primary.id}')
        return self.client.post('/neostaffing/app-management/people', data={**data, **overrides})

    def new_person(self):
        return StaffingPerson.query.filter_by(employee_id='NEW').one()

    def test_twenty_c_requires_primary_and_rejects_invalid_secondary_atomically(self):
        day = service.create_unit({'unit_type':'sort', 'name':'Day'})
        day_operation = service.create_unit({'unit_type':'operation','name':'Ramp','parent_id':day.id})
        day_department = service.create_unit({'unit_type':'department','name':'Shift','parent_id':day_operation.id})
        day_supervisor = self.person('DAY-FT')
        db.session.add(StaffingLeadershipAssignment(person=day_supervisor,unit=day_department,leadership_level='department'))
        db.session.commit()
        for values in ({'twenty_c_primary':''},
                       {'twenty_c_secondary':f'{self.sort.id}:{self.primary.id}'},
                       {'twenty_c_secondary':f'{day.id}:{day_supervisor.id}'},
                       {'twenty_c_secondary':f'{self.sort.id}:{day_supervisor.id}'},
                       {'twenty_c_primary':f'{day.id}:{self.primary.id}'},
                       {'twenty_c_primary':'bad'},
                       {'twenty_c_secondary':f'{self.sort.id}:999999'}):
            with self.subTest(values=values):
                response = self.post(**values)
                self.assertEqual(response.status_code, 302)
                self.assertIsNone(StaffingPerson.query.filter_by(employee_id='NEW').first())
                self.assertEqual(StaffingTwentyCAffiliation.query.count(), 0)
                self.assertEqual(StaffingReportingRelationship.query.count(), 0)

    def test_inactive_or_non_ft_supervisor_rejected(self):
        for attr, value in (('active',False), ('classification','manager')):
            with self.subTest(attr=attr):
                setattr(self.secondary, attr, value)
                db.session.commit()
                self.post(twenty_c_secondary=f'{self.sort.id}:{self.secondary.id}')
                self.assertIsNone(StaffingPerson.query.filter_by(employee_id='NEW').first())
                setattr(self.secondary, attr, True if attr == 'active' else 'full_time_supervisor')
                db.session.commit()

    def test_primary_only_creates_affiliation_and_formal_reports_to(self):
        self.post()
        person = self.new_person()
        rows = StaffingTwentyCAffiliation.query.filter_by(twenty_c_person_id=person.id,active=True).all()
        self.assertEqual([(row.affiliation_type,row.ft_supervisor_person_id) for row in rows], [('primary',self.primary.id)])
        self.assertEqual(StaffingReportingRelationship.query.filter_by(person_id=person.id,active=True).one().reports_to_person_id,self.primary.id)

    def test_primary_secondary_creates_two_but_reports_to_primary_only(self):
        self.post(twenty_c_secondary=f'{self.sort.id}:{self.secondary.id}', reports_to_person_id=self.secondary.id)
        person = self.new_person()
        rows = StaffingTwentyCAffiliation.query.filter_by(twenty_c_person_id=person.id,active=True).all()
        self.assertEqual({(r.affiliation_type,r.ft_supervisor_person_id) for r in rows}, {('primary',self.primary.id),('secondary',self.secondary.id)})
        self.assertEqual(StaffingReportingRelationship.query.filter_by(person_id=person.id,active=True).one().reports_to_person_id,self.primary.id)

    def assert_blank_form(self, response, flow):
        self.assertEqual(response.status_code,302)
        query = parse_qs(urlparse(response.location).query)
        self.assertNotIn('person_id',query)
        self.assertEqual(query['add'],[flow])
        html = self.client.get(response.location).get_data(as_text=True)
        self.assertRegex(html, f'data-people-add-drawer="{flow}" open')
        self.assertNotIn('<aside class="neostaffing-people-detail-drawer">',html)
        form = re.search(r'<form[^>]*class="[^"]*neostaffing-people-'+flow+r'-form"[^>]*>(.*?)</form>',html,re.S).group(1)
        for name in ('employee_id','first_name','last_name','seniority_date','phone_number'):
            self.assertRegex(form, f'name="{name}" value=""')
        self.assertRegex(form,r'name="classification"[^>]*><option value="">')
        self.assertNotRegex(form,r'<option[^>]* selected')
        return query, form

    def test_management_success_reopens_blank_form_preserving_filters(self):
        filters = dict(sort_id=str(self.sort.id),department_id=str(self.department.id),search='Supervisor',
                       classification='manager',employee_status='active',active='all',leadership_only='1',per_page='25')
        response=self.post(initial_assignment_unit_ids=[str(self.area.id)],
            twenty_c_secondary=f'{self.sort.id}:{self.secondary.id}', **{'people_filter_'+k:v for k,v in filters.items()})
        query, form=self.assert_blank_form(response,'management')
        for key,value in filters.items(): self.assertEqual(query[key],[value])
        self.assertNotIn('name="initial_assignment_unit_ids"',form)
        self.assertNotIn('data-scope-preselected',form)
        for name in ('reports_to_person_id','twenty_c_primary','twenty_c_secondary'):
            self.assertRegex(form,f'name="{name}"[^>]*><option value="">')
        self.assertIn('Primary FT Supervisor — Required',form)
        self.assertIn('Secondary FT Supervisor — Optional',form)

    def test_employee_success_reopens_blank_form_keeps_area_and_clears_shift_flow(self):
        response=self.post(creation_flow='employee',classification='part_time',
            initial_work_area_unit_id=str(self.area.id),
            shift_flow_setup_work_area_id=str(self.area.id),
            shift_flow_sort_start_work_area_id=str(self.area.id),
            shift_flow_final_door_work_area_id=str(self.area.id),
            people_filter_work_area_id=str(self.area.id),people_filter_classification='',people_filter_search='Roster')
        query,form=self.assert_blank_form(response,'employee')
        self.assertEqual(query['work_area_id'],[str(self.area.id)])
        self.assertEqual(query['search'],['Roster'])
        self.assertNotIn('classification',query)
        self.assertIn(f'name="initial_work_area_unit_id" value="{self.area.id}"',form)
        for name in ('setup_work_area_id','sort_start_work_area_id','final_door_work_area_id','ballmat_transition'):
            self.assertRegex(form,f'name="shift_flow_{name}"[^>]*><option value="">')
        self.assertIsNotNone(self.new_person().shift_flow_plan)

    def test_labels_cover_all_levels_dedupe_and_use_hierarchy_order(self):
        leader=self.person('LABELS','division_manager')
        for unit in (self.area,self.department,self.sort,self.operation):
            db.session.add(StaffingLeadershipAssignment(person=leader,unit=unit,leadership_level=unit.unit_type))
        stale=StaffingUnit(name='Old Area',unit_type='work_area',parent=self.department,active=False)
        db.session.add(stale)
        db.session.add(StaffingLeadershipAssignment(person=leader,unit=stale,leadership_level='work_area'))
        db.session.commit()
        expected=['Night',f'Night · {self.operation.name}','Night · Shift','Shift · Door 34']
        assignments=StaffingLeadershipAssignment.query.filter_by(person_id=leader.id).all()
        units={u.id:u for u in StaffingUnit.query.all()}
        self.assertEqual(service._management_scope_labels(assignments + assignments,units)[leader.id],expected)
        context=service.management_org_chart_context(leader.id)
        self.assertEqual(context['management_scope_labels'][leader.id],expected)
        self.assertEqual(service.org_chart_context(self.area.id)['management_scope_labels'][leader.id],expected)
        for url in (f'/neostaffing/org-chart?view=management&person_id={leader.id}',f'/neostaffing/org-chart?unit_id={self.area.id}'):
            html=self.client.get(url).get_data(as_text=True)
            self.assertIn('Night · Shift',html)
            self.assertIn('Shift · Door 34',html)
            self.assertNotIn('No active Work Area assignment',html)
        unassigned=self.person('UNASSIGNED')
        db.session.commit()
        html=self.client.get(f'/neostaffing/org-chart?view=management&person_id={unassigned.id}').get_data(as_text=True)
        self.assertIn('No active management assignment',html)
