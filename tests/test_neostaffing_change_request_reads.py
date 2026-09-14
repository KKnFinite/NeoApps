"""Real request-page query/hydration and legacy visibility equivalence proof."""
from collections import Counter
from datetime import datetime, timedelta
import json
import unittest
from flask import g
from sqlalchemy import event
from app.extensions import db
from app.models import StaffingPerson, StaffingWorkAssignment, StaffingChangeRequest, StaffingChangeRequestItem, StaffingLeadershipAssignment, StaffingTwentyCAffiliation, StaffingUnit, User
from app.services import neostaffing_change_requests as service
from tests import test_neostaffing_change_requests as fixtures


class ChangeRequestReadsTest(unittest.TestCase):
    _unit = fixtures.NeoStaffingChangeRequestsTest._unit
    _person = fixtures.NeoStaffingChangeRequestsTest._person
    _user = fixtures.NeoStaffingChangeRequestsTest._user
    _login = fixtures.NeoStaffingChangeRequestsTest._login
    tearDown = fixtures.NeoStaffingChangeRequestsTest.tearDown

    def setUp(self):
        fixtures.NeoStaffingChangeRequestsTest.setUp(self)
        self.division_user = self._user('division_reader','operator',self.division_manager)
        self.no_scope_user = self._user('unscoped_reader','operator')
        self.actor_ids = {name:getattr(self,name).id for name in
            ('source_approver_user','manager_user','division_user','no_scope_user','submitter_user')}
        self.expected = {}
        for index in range(205):
            person = self._person(f'READ-{index}', 'part_time', 'Read', f'{index:03}')
            area = self.source_area if index % 2 == 0 else self.destination_area
            db.session.add(StaffingWorkAssignment(person=person,work_area=area,active=True))
            row = StaffingChangeRequest(person=person, submitted_by_user_id=self.submitter_user.id,
                source_work_area_unit_id=area.id, status='pending',submitted_at=datetime.utcnow(),
                routed_approver_person_ids_json=json.dumps([self.source_approver.id] if index%2==0 else []),
                unassigned_approval=index%2==1)
            db.session.add(row); db.session.flush()
            db.session.add(StaffingChangeRequestItem(request_id=row.id,person_id=person.id,
                field_name='first_name',original_value_json='"Read"',requested_value_json='"New"',status='pending'))
            self.expected[row.id] = area.id
        db.session.commit()

    def profile(self, actor_id, queue):
        g.pop('_login_user',None); db.session.remove()
        actor = db.session.get(User,actor_id)
        self._login(actor)
        g.pop('_login_user',None); db.session.remove()
        sql, hydrated = [], Counter()
        def capture(_c,_cursor,statement,*args): sql.append(statement)
        def loaded(session,obj): hydrated[type(obj).__name__] += 1
        event.listen(db.engine,'before_cursor_execute',capture)
        event.listen(db.session.session_factory,'loaded_as_persistent',loaded)
        try:
            page = self.client.get('/neostaffing/requests',query_string={'queue':queue})
            self.assertEqual(page.status_code,200)
        finally:
            event.remove(db.engine,'before_cursor_execute',capture)
            event.remove(db.session.session_factory,'loaded_as_persistent',loaded)
        counts = Counter(s.split()[0].upper() for s in sql)
        return dict(counts), dict(hydrated)

    def test_profile_pages(self):
        # Before: 22/22/20/20/20 reads respectively, 205 request/item rows,
        # 211 people and 206 work assignments even for empty My Purview.
        budgets = dict(zip(self.actor_ids, (21, 21, 19, 15, 19)))
        for actor in self.actor_ids:
            counts, hydrated = self.profile(self.actor_ids[actor], 'purview' if actor!='submitter_user' else 'all')
            print(actor, json.dumps({'sql':counts,'hydrated':hydrated},sort_keys=True))
            self.assertLessEqual(counts.get('SELECT', 0) + counts.get('WITH', 0), budgets[actor])
            self.assertEqual(sum(counts.get(verb, 0) for verb in ('INSERT', 'UPDATE', 'DELETE')), 0)
            self.assertLessEqual(hydrated.get('StaffingChangeRequest', 0), 101)
            self.assertLessEqual(hydrated.get('StaffingChangeRequestItem', 0), 100)
            self.assertEqual(hydrated.get('StaffingWorkAssignment', 0), 0)
            self.assertLessEqual(hydrated.get('StaffingLeadershipAssignment', 0), 1)
            self.assertLessEqual(hydrated.get('StaffingPerson', 0), 156 if actor == 'submitter_user' else 102)
            if actor == 'no_scope_user':
                for model in ('StaffingPerson', 'StaffingChangeRequest', 'StaffingChangeRequestItem'):
                    self.assertEqual(hydrated.get(model, 0), 0)
        counts, hydrated = self.profile(self.actor_ids['source_approver_user'], '')
        # The FT default is Routed: one skinny legacy-JSON matching projection
        # keeps its measured original 23-read budget (including routed retention
        # matching) while bounding full detail hydration.
        self.assertLessEqual(counts.get('SELECT', 0) + counts.get('WITH', 0), 23)
        self.assertLessEqual(hydrated.get('StaffingChangeRequest', 0), 100)

    def visible_ids(self, user, **filters):
        ids = []
        for page in range(1, 10):
            context = service.change_request_context(dict(filters, page=page), user)
            self.assertLessEqual(len(context['rows']), service.REQUEST_PAGE_SIZE)
            ids.extend(row['request'].id for row in context['rows'])
            if not context['pagination']['has_next']:
                return ids
        self.fail('Pagination did not terminate')

    def legacy_ids(self, user, queue, search=''):
        people = StaffingPerson.query.order_by(StaffingPerson.last_name, StaffingPerson.first_name,
                                               StaffingPerson.employee_id, StaffingPerson.id).all()
        person = service._person_for_user_from_rows(user, people)
        by_person = {}
        for row in StaffingLeadershipAssignment.query.filter_by(active=True):
            by_person.setdefault(row.person_id, []).append(row)
        authority = service._management_authority_unit_ids_from_rows(
            by_person, StaffingTwentyCAffiliation.query.filter_by(active=True).all())
        units = {row.id: row for row in StaffingUnit.query.all()}
        people = {row.id: row for row in people}
        return [row.id for row in StaffingChangeRequest.query.order_by(
            StaffingChangeRequest.submitted_at, StaffingChangeRequest.id)
            if service._request_matches_queue(row, queue, person,
                service._decode_person_ids(row.routed_approver_person_ids_json), authority, units)
            and search.lower() in f'{people[row.person_id].full_name} {people[row.person_id].employee_id} {row.id}'.lower()]

    def test_scope_union_routing_search_and_pagination_match_legacy(self):
        # Distinguish Department, Operation and Sort boundaries, including a
        # cross-boundary request visible through its destination, not its source.
        other_operation = self._unit('operation', 'Other Operation', self.sort)
        other_department = self._unit('department', 'Other Department', other_operation)
        other_area = self._unit('work_area', 'Other Area', other_department)
        other_sort = self._unit('sort', 'Other Sort')
        outside_operation = self._unit('operation', 'Outside Operation', other_sort)
        outside_department = self._unit('department', 'Outside Department', outside_operation)
        outside_area = self._unit('work_area', 'Outside Area', outside_department)
        requests = StaffingChangeRequest.query.order_by(StaffingChangeRequest.id).limit(3).all()
        requests[0].source_work_area_unit_id = other_area.id
        requests[1].source_work_area_unit_id = outside_area.id
        requests[2].source_work_area_unit_id = outside_area.id
        requests[2].destination_work_area_unit_id = self.source_area.id
        requests[0].routed_approver_person_ids_json = json.dumps([str(self.source_approver.id)])
        requests[1].routed_approver_person_ids_json = 'invalid legacy JSON'
        combo = self._person('20C-READ', 'twenty_c_full_time_supervisor', 'Twenty', 'Reader')
        combo_user = self._user('twenty_reader', 'operator', combo)
        db.session.add(StaffingTwentyCAffiliation(twenty_c_person=combo,
            ft_supervisor_person=self.destination_approver, sort_unit=self.sort,
            affiliation_type='primary', active=True))
        db.session.add(StaffingLeadershipAssignment(person=combo, unit=other_department,
            leadership_level='department', active=True))
        db.session.commit()
        users = [self.source_approver_user, self.manager_user, self.division_user,
                 self.no_scope_user, combo_user]
        for user in users:
            for queue in ('purview', 'routed', 'unassigned', 'all'):
                for search in ('', 'Read 00', '%'):
                    with self.subTest(user=user.username, queue=queue, search=search):
                        self.assertEqual(self.visible_ids(user, queue=queue, search=search),
                                         self.legacy_ids(user, queue, search))
        db.session.add(StaffingLeadershipAssignment(person=self.source_approver,
            unit=self.destination_department, leadership_level='department', active=True))
        db.session.commit()
        self.assertEqual(self.visible_ids(self.source_approver_user, queue='purview'),
                         self.legacy_ids(self.source_approver_user, 'purview'))
        self.assertEqual(len(self.visible_ids(self.no_scope_user, queue='all')), 205)
        self.assertEqual(self.visible_ids(self.no_scope_user, queue='purview'), [])

    def test_no_n_plus_one(self):
        before, _ = self.profile(self.actor_ids['manager_user'], 'purview')
        keep = min(self.expected)
        StaffingChangeRequestItem.query.filter(StaffingChangeRequestItem.request_id != keep).delete()
        StaffingChangeRequest.query.filter(StaffingChangeRequest.id != keep).delete()
        db.session.commit()
        after, _ = self.profile(self.actor_ids['manager_user'], 'purview')
        self.assertEqual(before, after)

    def test_history_window_badge_and_pending_order_are_unchanged(self):
        rows = StaffingChangeRequest.query.order_by(StaffingChangeRequest.id).limit(3).all()
        now = datetime.utcnow()
        recent, expired, overdue = rows
        recent.status = expired.status = 'completed'
        recent.completed_at = now - timedelta(days=1)
        expired.completed_at = now - timedelta(days=15)
        overdue.submitted_at = now - timedelta(days=3)
        db.session.commit()
        self.assertEqual(self.visible_ids(self.manager_user, queue='all', view='history'), [recent.id])
        all_ids = self.visible_ids(self.manager_user, queue='all', view='all')
        self.assertEqual(all_ids[0], overdue.id)
        self.assertEqual(all_ids[-1], recent.id)
        self.assertNotIn(expired.id, all_ids)
        self.assertNotIn(recent.id, self.visible_ids(self.manager_user, queue='all'))
        context = service.change_request_context({'queue':'purview', 'search':'no match', 'page':'invalid'}, self.manager_user)
        self.assertEqual(context['rows'], [])
        self.assertEqual(context['unassigned_count'], 101)
        self.assertEqual(context['pagination']['page'], 1)

    def test_real_page_navigation_and_detail_fields(self):
        self._login(self.manager_user)
        page = self.client.get('/neostaffing/requests?queue=purview')
        html = page.get_data(as_text=True)
        self.assertEqual(html.count('data-change-request-id='), 100)
        self.assertIn('aria-label="Request pages"', html)
        self.assertIn('page=2', html)
        self.assertIn('name="page" value="1"', html)
        last = self.client.get('/neostaffing/requests?queue=purview&page=3').get_data(as_text=True)
        self.assertEqual(last.count('data-change-request-id='), 5)
        self.assertIn('PREVIOUS', last)
        self.assertNotIn('>NEXT</a>', last)
        self.assertIn('Read 204', last)
