"""Cold-session leadership aggregation correctness and SQL cost; no external I/O."""
import json
import os
from datetime import date
from pathlib import Path
from statistics import median
from time import perf_counter
import unittest

from sqlalchemy import event

from app import create_app
from app.extensions import db
from app.models import StaffingLeadershipAssignment, StaffingPerson, StaffingUnit
from app.models.staffing_person import STAFFING_DATABASE_CLASSIFICATIONS
from app.services import neostaffing as service


def legacy_index():
    """Frozen pre-N12 algorithm for equivalence and before/after measurements."""
    index = {}
    assignments = StaffingLeadershipAssignment.query.join(StaffingPerson).filter(
        StaffingLeadershipAssignment.active.is_(True), StaffingPerson.active.is_(True)
    ).all()
    for assignment in assignments:
        counts = index.setdefault(assignment.unit_id, {})
        classification = assignment.person.classification
        counts[classification] = counts.get(classification, 0) + 1
    return index


class LeadershipQueryTest(unittest.TestCase):
    def setUp(self):
        config = type('LeadershipConfig', (), dict(
            SECRET_KEY='isolated-test', TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            SQLALCHEMY_TRACK_MODIFICATIONS=False))
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.next_person = 0

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def unit(self, name='Area', unit_type='work_area', parent=None):
        unit = StaffingUnit(name=name, unit_type=unit_type,
                            parent_id=parent.id if parent else None, required_headcount=4)
        db.session.add(unit)
        db.session.flush()
        return unit

    def leader(self, unit, classification='part_time_supervisor', *, person_active=True,
               assignment_active=True, employee_status='active'):
        self.next_person += 1
        person = StaffingPerson(employee_id=f'N12-{self.next_person}', first_name='Synthetic',
            last_name=f'Leader {self.next_person}', seniority_date=date(2020, 1, 1),
            classification=classification, active=person_active, employee_status=employee_status)
        db.session.add(person)
        db.session.flush()
        db.session.add(StaffingLeadershipAssignment(person_id=person.id, unit_id=unit.id,
            leadership_level=unit.unit_type, active=assignment_active))
        return person

    def measure(self, function):
        # The identity map must not mask the original per-person lazy loads.
        db.session.expunge_all()
        statements = []
        def record(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)
        event.listen(db.engine, 'before_cursor_execute', record)
        started = perf_counter()
        try:
            result = function()
        finally:
            elapsed = (perf_counter() - started) * 1000
            event.remove(db.engine, 'before_cursor_execute', record)
        return result, statements, elapsed

    def population(self, count):
        units = [self.unit('Area A'), self.unit('Area B')]
        classifications = ('part_time_supervisor', 'full_time_supervisor',
                           'twenty_c_full_time_supervisor', 'manager', 'division_manager')
        for number in range(count):
            self.leader(units[number % 2], classifications[number % len(classifications)])
        db.session.commit()
        measurements = {'leaders': count, 'dialect': db.engine.dialect.name}
        expected = None
        for label, function in (('before', legacy_index), ('after', service._board_leadership_index)):
            samples = []
            for _ in range(5):
                result, statements, elapsed = self.measure(function)
                if expected is None:
                    expected = result
                self.assertEqual(result, expected)
                selects = [sql for sql in statements if sql.lstrip().upper().startswith('SELECT')]
                self.assertEqual(len(selects), count + 1 if label == 'before' else 1)
                self.assertEqual(len(statements), len(selects), 'Aggregation must never write')
                samples.append(elapsed)
                if label == 'after':
                    self.assertIn('GROUP BY', selects[0])
                    self.assertNotIn('staffing_people.first_name', selects[0])
                    self.assertEqual(len(db.session.identity_map), 0, 'No employee/assignment hydration')
            measurements[label] = {'selects': len(selects), 'writes': 0,
                                   'median_ms': round(median(samples), 3)}
        output = os.environ.get('N12_MEASUREMENTS_DIR')
        if output:
            destination = Path(output)
            destination.mkdir(parents=True, exist_ok=True)
            (destination / f'{db.engine.dialect.name}-{count}.json').write_text(
                json.dumps(measurements, indent=2), encoding='utf8')

    def test_empty(self):
        self.population(0)

    def test_one(self):
        self.population(1)

    def test_five(self):
        self.population(5)

    def test_fifty(self):
        self.population(50)

    def test_active_filters_and_status_semantics(self):
        unit = self.unit()
        unit_id = unit.id
        self.leader(unit)
        self.leader(unit, person_active=False)
        self.leader(unit, assignment_active=False)
        self.leader(unit, employee_status='fmla')  # Existing contract filters active, not status.
        db.session.commit()
        result, sql, _ = self.measure(service._board_leadership_index)
        self.assertEqual(result, {unit_id: {'part_time_supervisor': 2}})
        self.assertEqual(len(sql), 1)

    def test_all_database_classifications_remain_distinct(self):
        unit = self.unit()
        unit_id = unit.id
        for classification in STAFFING_DATABASE_CLASSIFICATIONS:
            self.leader(unit, classification)
        db.session.commit()
        result, _, _ = self.measure(service._board_leadership_index)
        self.assertEqual(result, {unit_id: dict.fromkeys(STAFFING_DATABASE_CLASSIFICATIONS, 1)})

    def test_counts_assignment_rows_not_distinct_people(self):
        unit = self.unit()
        person = self.leader(unit)
        # Database-valid legacy scopes count separately, as the old helper did.
        db.session.add(StaffingLeadershipAssignment(person_id=person.id, unit_id=unit.id,
            leadership_level='department', active=True))
        self.leader(unit)
        unit_id = unit.id
        db.session.commit()
        result, _, _ = self.measure(service._board_leadership_index)
        self.assertEqual(result, {unit_id: {'part_time_supervisor': 3}})
        self.assertEqual(result, legacy_index())

    def test_reassignment_and_classification_changes_are_not_cached(self):
        first, second = self.unit('First'), self.unit('Second')
        person = self.leader(first)
        ids = first.id, second.id, person.id
        db.session.commit()
        self.assertEqual(service._board_leadership_index(), {ids[0]: {'part_time_supervisor': 1}})
        assignment = StaffingLeadershipAssignment.query.one()
        assignment.unit_id = ids[1]
        person.classification = 'twenty_c_full_time_supervisor'
        db.session.commit()
        result, statements, _ = self.measure(service._board_leadership_index)
        self.assertEqual(result, {ids[1]: {'twenty_c_full_time_supervisor': 1}})
        self.assertEqual(len(statements), 1)

    def test_hierarchy_counts_and_missing_warnings_are_equivalent(self):
        sort = self.unit('Sort', 'sort')
        operation = self.unit('Operation', 'operation', sort)
        department = self.unit('Department', 'department', operation)
        area = self.unit('Area', 'work_area', department)
        empty = self.unit('Empty', 'work_area', department)
        for unit, classification in ((area, 'part_time_supervisor'),
            (area, 'twenty_c_full_time_supervisor'), (department, 'full_time_supervisor'),
            (department, 'twenty_c_full_time_supervisor'), (operation, 'manager'),
            (sort, 'division_manager')):
            self.leader(unit, classification)
        db.session.commit()
        # Holding unit references here is deliberate: only the aggregation is measured.
        index = service._board_leadership_index()
        self.assertEqual(index, legacy_index())
        counts = service._board_work_area_leadership_counts(index, sort, operation, department, area)
        self.assertEqual(counts, dict(pt_supervisors=2, ft_supervisors=2, managers=1, division_managers=1))
        self.assertEqual(service._board_missing_leadership(counts), [])
        missing = service._board_work_area_leadership_counts(index, sort, operation, department, empty)
        self.assertEqual(service._board_missing_leadership(missing), ['PT Supervisor'])
        self.assertEqual(service._board_missing_leadership(dict.fromkeys(counts, 0)),
                         ['PT Supervisor', 'FT Supervisor', 'Manager', 'Division Manager'])


if __name__ == '__main__':
    unittest.main()
