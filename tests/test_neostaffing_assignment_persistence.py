"""Real HTTP assignment writes must reject a stale page, not just serialize it."""
import unittest
from flask import g
from app.extensions import db
from app.models import StaffingPerson, StaffingWorkAssignment
from app.services import neostaffing as staffing
from tests import test_neostaffing_shift_authority as authority
from tests import test_neostaffing_routes as routes


class AssignmentPersistenceTest(unittest.TestCase):
    person = authority.ShiftAuthorityTest.person
    revision = authority.ShiftAuthorityTest.revision
    _user = routes.NeoStaffingRoutesTest._user
    _grant_app_access = routes.NeoStaffingRoutesTest._grant_app_access
    _login = routes.NeoStaffingRoutesTest._login
    tearDown = authority.ShiftAuthorityTest.tearDown

    def setUp(self):
        authority.ShiftAuthorityTest.setUp(self)
        from app.services.access_control import ensure_default_gateway_and_nodes
        from app.services.permission_rules import ensure_default_permission_rules
        ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        actor = self._user('assignment_verifier')
        self._grant_app_access(actor, 'neostaffing', 'simulator')
        self.username = actor.username
        db.session.commit()
        self.client = self.app.test_client()
        self._login(self.username)

    def worker(self, classification='part_time'):
        person = self.person(classification)
        staffing.assign_work_area(person, self.areas['Door 6'])
        db.session.commit()
        return person

    def saved_areas(self, person_id):
        g.pop('_login_user', None)
        db.session.remove()
        return set(db.session.scalars(db.select(StaffingWorkAssignment.work_area_unit_id).where(
            StaffingWorkAssignment.person_id == person_id, StaffingWorkAssignment.active.is_(True))))

    def test_single_stale_page_cannot_overwrite_committed_move(self):
        person = self.worker()
        pid, old = person.id, staffing.assignment_service.version(person)
        first, second = self.areas['Door 9'].id, self.areas['Door 13'].id
        url = f'/neostaffing/people/{pid}/assign-work-area'
        self.client.post(url, data={'work_area_unit_id': first, 'expected_assignment_version': old})
        self.assertEqual(self.saved_areas(pid), {first})
        response = self.client.post(url, data={'work_area_unit_id': second, 'expected_assignment_version': old}, follow_redirects=True)
        self.assertEqual(self.saved_areas(pid), {first})
        self.assertIn(b'changed while', response.data)

    def test_bulk_stale_selection_rolls_back_every_employee(self):
        first, second = self.worker(), self.worker()
        ids = [first.id, second.id]
        versions = {f'expected_assignment_version_{p.id}': staffing.assignment_service.version(p) for p in (first, second)}
        original, newer, target = [self.areas[name].id for name in ('Door 6','Door 9','Door 13')]
        staffing.assign_work_area(second, self.areas['Door 9'])
        db.session.commit()
        response = self.client.post('/neostaffing/people/bulk-work-area', data={
            'bulk_action':'move', 'person_ids':ids, 'work_area_unit_id':target, **versions}, follow_redirects=True)
        self.assertEqual(self.saved_areas(ids[0]), {original})
        self.assertEqual(self.saved_areas(ids[1]), {newer})
        self.assertIn(b'changed while', response.data)

    def test_bulk_ft_assignments_survive_reload_and_fresh_login(self):
        person = self.worker('full_time_combo')
        staffing.assign_work_area(person, self.other)
        peer = self.worker()
        db.session.commit()
        pid, peer_id, target, other_id = person.id, peer.id, self.areas['Door 9'].id, self.other.id
        page = self.client.get(f'/neostaffing/people?work_area_id={self.areas["Door 6"].id}')
        self.assertIn(f'name="expected_assignment_version_{pid}"'.encode(), page.data)
        response = self.client.post('/neostaffing/people/bulk-work-area', data={
            'bulk_action':'move', 'person_ids':[pid, peer_id], 'work_area_unit_id':target,
            **{f'expected_assignment_version_{p.id}':staffing.assignment_service.version(p) for p in (person, peer)}}, follow_redirects=True)
        self.assertIn(b'updated 2 people', response.data)
        self.assertEqual(self.saved_areas(pid), {target, other_id})
        self.assertEqual(self.saved_areas(peer_id), {target})
        fresh = self.app.test_client()
        self._login(self.username, client=fresh)
        for client in (self.client, fresh):
            html = client.get(f'/neostaffing/people?work_area_id={target}').data
            self.assertIn(f'name="person_ids" value="{pid}"'.encode(), html)
            self.assertIn(f'name="person_ids" value="{peer_id}"'.encode(), html)
        self.assertEqual(self.saved_areas(pid), {target, other_id})

    def test_manual_home_editor_then_drag_and_stale_editor(self):
        person = self.worker('full_time_combo')
        staffing.assign_work_area(person, self.other)
        db.session.commit()
        pid, other_id = person.id, self.other.id
        original = self.revision(person)
        home_id = self.areas['East Ballmat'].id
        response = self.client.post(f'/neostaffing/shift-flow/{pid}', data={
            'expected_version':original, 'shift_flow_sort_start_work_area_id':home_id,
            'shift_flow_final_door_work_area_id':self.areas['Door 9'].id,
            'shift_flow_ballmat_transition':'1'}, follow_redirects=True)
        self.assertIn(b'Shift Flow plan saved', response.data)
        self.assertEqual(self.saved_areas(pid), {home_id,other_id})
        person = db.session.get(StaffingPerson,pid)
        newer = self.revision(person)
        response = self.client.post(f'/neostaffing/shift-flow/{pid}/final-composite', json={
            'expected_version':newer, 'final_door_id':self.areas['Door 24'].id,
            'band':'bm2', 'complete_route':True})
        self.assertEqual(response.status_code,200, response.data)
        target = self.areas['West Ballmat'].id
        self.assertEqual(self.saved_areas(pid), {target,other_id})
        response = self.client.post(f'/neostaffing/shift-flow/{pid}', data={
            'expected_version':newer, 'shift_flow_sort_start_work_area_id':home_id}, follow_redirects=True)
        self.assertIn(b'changed while', response.data)
        self.assertEqual(self.saved_areas(pid), {target,other_id})
        fresh = self.app.test_client()
        self._login(self.username, client=fresh)
        self.assertEqual(fresh.get(f'/neostaffing/shift-flow?person_id={pid}').status_code,200)
        self.assertEqual(db.session.get(StaffingPerson,pid).shift_flow_plan.sort_start_work_area_id,target)

    def test_missing_version_and_stale_clear_are_rejected(self):
        person = self.worker()
        pid, old = person.id, staffing.assignment_service.version(person)
        origin, newer = self.areas['Door 6'].id, self.areas['Door 9'].id
        self.client.post(f'/neostaffing/people/{pid}/assign-work-area', data={'work_area_unit_id':newer})
        self.assertEqual(self.saved_areas(pid), {origin})
        self.client.post('/neostaffing/app-management/work-assignments/assign', data={
            'person_id':pid, 'work_area_unit_id':newer, 'expected_assignment_version':old})
        self.assertEqual(self.saved_areas(pid), {newer})
        for url in (f'/neostaffing/people/{pid}/clear-work-area',f'/neostaffing/app-management/work-assignments/{pid}/clear'):
            self.client.post(url, data={'expected_assignment_version':old})
            self.assertEqual(self.saved_areas(pid), {newer})

    def test_people_profile_save_preserves_ft_sort_assignments(self):
        person = self.worker('full_time_combo')
        staffing.assign_work_area(person, self.other)
        db.session.commit()
        pid, areas = person.id, {self.areas['Door 6'].id,self.other.id}
        response = self.client.post(f'/neostaffing/app-management/people/{pid}/update', data={
            'employee_id':person.employee_id, 'first_name':'Updated', 'last_name':person.last_name,
            'classification':person.classification, 'seniority_date':'2020-01-01', 'active':'1'}, follow_redirects=True)
        self.assertIn(b'Person updated', response.data)
        self.assertEqual(self.saved_areas(pid), areas)
        fresh = self.app.test_client()
        self._login(self.username, client=fresh)
        self.assertIn(b'Updated',fresh.get(f'/neostaffing/people?person_id={pid}').data)
        self.assertEqual(self.saved_areas(pid), areas)
