"""Fresh-request SQL/hydration instrumentation; no external database or traffic."""
from collections import Counter
from datetime import date, timedelta
import re
import unittest

from flask import g
from sqlalchemy import event
from sqlalchemy.orm import Session
from app.extensions import db
from app.models import (Gateway, NeoErmacBuildingLineup, SortDateOperation,
    StaffingUnit, StaffingPerson, StaffingWorkAssignment, StaffingShiftFlowPlan, StaffingDailyAttendance)
from tests import test_neoermac_routes as fixtures
from tests.neoermac_lineup_forms import lineup_form
from tests.neoermac_pull_forms import pull_form
from app.services.neoermac_building_lineup import get_building_lineup_rows


class NeoErmacReadBudgetTest(unittest.TestCase):
    _add_master_departure = fixtures.NeoErmacRoutesTest._add_master_departure
    _add_operation_departure = fixtures.NeoErmacRoutesTest._add_operation_departure
    _assign_lineup_destination = fixtures.NeoErmacRoutesTest._assign_lineup_destination
    _login_approved_user = fixtures.NeoErmacRoutesTest._login_approved_user
    tearDown = fixtures.NeoErmacRoutesTest.tearDown

    def setUp(self):
        fixtures.NeoErmacRoutesTest.setUp(self)
        for flight, dest in [('UPS501','SDF'), ('UPS502','ONT'), ('UPS503','PHX')]:
            self._add_master_departure(flight, dest)
        mission = self._add_operation_departure('UPS501', 'SDF', tail='N501UP')
        self.operation_id = mission.sort_date_operation_id
        self.gateway_id = self.gateway.id
        self._assign_lineup_destination('green_runout', 'east_destination_1', 'SDF')
        self._assign_lineup_destination('green_runout', 'west_destination_1', 'PHX')
        self._login_approved_user(role='simulator')
        self.client.post('/neoermac/door-view/supervision', data={'doors':['D1'], 'active_door':'D1'})
        night = StaffingUnit(unit_type='sort', name='Night')
        ramp = StaffingUnit(unit_type='operation', name='Ramp', parent=night)
        shift = StaffingUnit(unit_type='department', name='Shift', parent=ramp)
        door = StaffingUnit(unit_type='work_area', name='Door 1', parent=shift)
        other = StaffingUnit(unit_type='work_area', name='Door 4', parent=shift)
        ballmat = StaffingUnit(unit_type='work_area', name='East Ballmat', parent=shift)
        db.session.add_all([night,ramp,shift,door,other,ballmat]); db.session.flush()
        self.door_id, self.other_id = door.id, other.id
        operation = db.session.get(SortDateOperation, self.operation_id)
        for i in range(100):
            person = StaffingPerson(employee_id=f'ER{i:03}',first_name='Employee',last_name=f'{i:03}',
                seniority_date=date(2020,1,1),classification='part_time',employee_status='active',active=True)
            db.session.add(person);db.session.flush()
            db.session.add(StaffingWorkAssignment(person=person,work_area=door if i < 5 else ballmat))
            db.session.add(StaffingShiftFlowPlan(person=person,sort_start_work_area=door if i < 5 else ballmat,
                final_door_work_area=door if i < 10 else other))
            if i in (0,5,10,11,12):
                db.session.add(StaffingDailyAttendance(person_id=person.id,sort_unit_id=night.id,
                    attendance_date=operation.sort_date - timedelta(days=1 if i==11 else 0),
                    sort_date_operation_id=None if i==12 else operation.id,
                    work_area_unit_id=other.id if i==0 else door.id,status='scheduled_off' if i==10 else 'here'))
        # Warm-sort baseline is explicit; GETs must not initialize this fixture.
        get_building_lineup_rows(self.gateway, initialize=True)
        db.session.commit()
        self.client.get('/neoermac/building-lineup')
        self.client.get('/neoermac/door-view/manage-employees')

    def measure(self, path, *, data=None):
        g.__dict__.clear();db.session.remove()
        statements=[];commits=[];hydrated=Counter()
        def sql(c, cursor, statement, *args): statements.append(statement)
        def commit(c): commits.append(True)
        def load(session, obj):
            if type(obj) in (StaffingPerson,StaffingWorkAssignment,StaffingShiftFlowPlan):
                hydrated[type(obj).__name__] += 1
        event.listen(db.engine,'before_cursor_execute',sql);event.listen(db.engine,'commit',commit)
        event.listen(Session,'loaded_as_persistent',load)
        try:
            response=self.client.get(path) if data is None else self.client.post(path,data=data)
        finally:
            event.remove(db.engine,'before_cursor_execute',sql);event.remove(db.engine,'commit',commit)
            event.remove(Session,'loaded_as_persistent',load)
        counts=Counter(s.lstrip().split()[0].upper() for s in statements)
        result={key:counts[key] for key in ('SELECT','INSERT','UPDATE','DELETE')}
        result.update(commits=len(commits),hydrated=dict(hydrated),status=response.status_code)
        result['select_tables']=dict(Counter(re.search(r'\bFROM\s+(\w+)',s,re.I).group(1)
            for s in statements if s.lstrip().upper().startswith('SELECT') and re.search(r'\bFROM\s+(\w+)',s,re.I)))
        return response,result

    def profile(self):
        results={}
        _,results['Lineup GET']=self.measure('/neoermac/building-lineup')
        response,results['Lineup changed state']=self.measure('/neoermac/building-lineup/state?revision=old')
        _,results['Lineup unchanged state']=self.measure('/neoermac/building-lineup/state?revision='+response.get_json()['revision'])
        form=lineup_form(db.session.get(Gateway,self.gateway_id),{'field':'lineup_green_runout_west_destination_1','destination':'ONT'})
        _,results['Lineup autosave']=self.measure('/neoermac/building-lineup/destination',data=form)
        form=pull_form(db.session.get(Gateway,self.gateway_id),{'door':'D1','destination':'SDF','pull_key':'pure','actual_pull':'01:45'})
        _,results['Door pull autosave']=self.measure('/neoermac/door-view/pull-autosave',data=form)
        _,results['Attendance GET']=self.measure('/neoermac/door-view/manage-employees')
        NeoErmacBuildingLineup.query.delete();db.session.commit()
        _,results['Cold Lineup GET']=self.measure('/neoermac/building-lineup')
        _,results['Repeated cold Lineup GET']=self.measure('/neoermac/building-lineup')
        return results

    def test_fresh_request_query_write_and_hydration_budgets(self):
        # Baseline (SELECT/INSERT/UPDATE/DELETE/commit): GET 20/0/0/0/0,
        # changed 16/0/0/0/0, unchanged 12/0/0/0/0, save 18/0/2/0/1,
        # pull 20/1/2/0/1, Attendance 21/0/0/0/0, cold 35/12/0/0/1.
        expected = {
            'Lineup GET': (19, 0, 0, 0, 0),
            'Lineup changed state': (11, 0, 0, 0, 0),
            'Lineup unchanged state': (8, 0, 0, 0, 0),
            'Lineup autosave': (15, 0, 2, 0, 1),
            'Door pull autosave': (19, 1, 2, 0, 1),
            'Attendance GET': (21, 0, 0, 0, 0),
            'Cold Lineup GET': (19, 0, 0, 0, 0),
            'Repeated cold Lineup GET': (19, 0, 0, 0, 0),
        }
        results = self.profile()
        for path, budget in expected.items():
            with self.subTest(path=path):
                metrics = results[path]
                self.assertEqual(metrics['status'], 200)
                self.assertEqual(tuple(metrics[k] for k in
                    ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'commits')), budget)
        self.assertEqual(results['Attendance GET']['hydrated'], {
            'StaffingPerson': 12, 'StaffingWorkAssignment': 12, 'StaffingShiftFlowPlan': 12,
        })
        self.assertEqual(results['Lineup autosave']['select_tables']['neoermac_building_lineups'], 1)
        self.assertEqual(results['Lineup autosave']['select_tables']['sort_date_missions'], 1)
        self.assertEqual(results['Lineup autosave']['select_tables']['master_flight_schedules'], 1)
        self.assertEqual(results['Door pull autosave']['select_tables']['neoermac_door_pulls'], 1)
        self.assertEqual(NeoErmacBuildingLineup.query.count(), 0)

    def test_cold_pages_leave_lineup_defaults_transient_until_explicit_write(self):
        NeoErmacBuildingLineup.query.delete()
        db.session.commit()
        for path in ('/neoermac/building-lineup', '/neoermac/upcoming-pulls',
                     '/neoermac/view-outbound', '/neoermac/door-view?door=D1'):
            for attempt in range(2):
                with self.subTest(path=path, attempt=attempt):
                    response, metrics = self.measure(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual([metrics[k] for k in ('INSERT', 'UPDATE', 'DELETE', 'commits')], [0]*4)
                    self.assertEqual(NeoErmacBuildingLineup.query.count(), 0)
        gateway = db.session.get(Gateway, self.gateway_id)
        field = 'lineup_green_runout_east_destination_1'
        form = lineup_form(gateway, {'field': field, 'destination': 'SDF'})
        response, metrics = self.measure('/neoermac/building-lineup/destination', data=form)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(metrics['INSERT'], 12)
        self.assertEqual(NeoErmacBuildingLineup.query.count(), 12)
        form = lineup_form(db.session.get(Gateway, self.gateway_id), {'field': field, 'destination': 'ONT'})
        response, metrics = self.measure('/neoermac/building-lineup/destination', data=form)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(metrics['INSERT'], 0)
        self.assertEqual(NeoErmacBuildingLineup.query.count(), 12)

    def test_lineup_snapshot_payload_and_post_aggregation_times_match_fresh_reads(self):
        from app.services.neoermac_building_lineup import (
            building_lineup_state_payload, get_departure_destination_choices,
            get_departure_destination_pull_times, get_destination_pull_times,
        )
        gateway = db.session.get(Gateway, self.gateway_id)
        expected_choices = get_departure_destination_choices(gateway)
        expected_times = get_departure_destination_pull_times(gateway)
        state = building_lineup_state_payload(gateway)
        self.assertEqual(state['destination_choices'], expected_choices)
        self.assertEqual(state['pull_times'], expected_times)
        response, _ = self.measure('/neoermac/building-lineup/state?revision=old')
        self.assertEqual(response.get_json()['state'], state)
        response, _ = self.measure('/neoermac/building-lineup/state?revision='+response.get_json()['revision'])
        self.assertFalse(response.get_json()['changed'])
        self.assertNotIn('state', response.get_json())
        gateway = db.session.get(Gateway, self.gateway_id)
        form = lineup_form(gateway, {'field': 'lineup_green_runout_west_destination_1', 'destination': 'SDF'})
        response, _ = self.measure('/neoermac/building-lineup/destination', data=form)
        self.assertEqual(response.status_code, 200)
        db.session.remove()
        self.assertEqual(response.get_json()['pull_times'],
            get_destination_pull_times(db.session.get(Gateway, self.gateway_id), 'SDF'))

    def test_scoped_attendance_matches_unscoped_here_coming_overrides_and_counts(self):
        from app.services.neostaffing import operational_manage_employees_context
        def snapshot(scoped):
            g.__dict__.clear()
            db.session.remove()
            context = operational_manage_employees_context(
                [self.door_id], later_final_area_ids=[self.door_id], scope_candidates=scoped,
            )
            return {
                'counts': context['counts'],
                **{key: [(r['person'].employee_id, r['status'], r['effective_work_area_id'], r['flow'])
                         for r in context[key]] for key in ('here', 'coming')},
            }
        baseline = snapshot(False)
        self.assertEqual(snapshot(True), baseline)
        self.assertEqual([r[0] for r in baseline['here']], ['ER001','ER002','ER003','ER004','ER005','ER010','ER012'])
        self.assertEqual([r[0] for r in baseline['coming']], ['ER000','ER006','ER007','ER008','ER009'])
        # Attendance-only candidate without a plan; moved-out and inactive cases
        # must retain the same effective-area and active-assignment semantics.
        person = StaffingPerson.query.filter_by(employee_id='ER012').one()
        db.session.delete(person.shift_flow_plan)
        StaffingPerson.query.filter_by(employee_id='ER001').one().active = False
        person = StaffingPerson.query.filter_by(employee_id='ER002').one()
        StaffingWorkAssignment.query.filter_by(person_id=person.id).one().active = False
        person = StaffingPerson.query.filter_by(employee_id='ER000').one()
        person.shift_flow_plan.final_door_work_area_id = self.other_id
        db.session.commit()
        baseline = snapshot(False)
        self.assertEqual(snapshot(True), baseline)
        self.assertIn('ER012', [r[0] for r in baseline['here']])
        self.assertNotIn('ER000', [r[0] for r in baseline['coming']])
