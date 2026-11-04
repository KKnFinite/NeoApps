"""W1 server authority, current-Sort isolation and read-only revision acceptance."""
import unittest
from datetime import date
from flask import g
from sqlalchemy import event
from app.extensions import db
from app.models import Gateway, GatewayMembership, GatewayNodeRole, NeoNode, PermissionRule, SortDateOperation, SortDateMission, NeoScorpionFuelAssignment
from tests import test_neoscorpion_live_assignments as scorpion
observations = {}

def fresh(client, path, method='GET', **kw):
    g.__dict__.clear()
    db.session.expire_all()
    return client.open(path, method=method, **kw)

def measured(client, path, **kw):
    sql = []

    def capture(c, u, s, *a):
        sql.append(s.strip())
    event.listen(db.engine, 'before_cursor_execute', capture)
    try:
        r = fresh(client, path, **kw)
    finally:
        event.remove(db.engine, 'before_cursor_execute', capture)
    return (r, dict(selects=sum((s.upper().startswith('SELECT') for s in sql)), writes=sum((s.upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for s in sql)), vendor_reads=sum(('neoscorpion_vendor_accounts' in s for s in sql))))

class RevisionSecurity(unittest.TestCase):

    def setUp(self):
        self.f = scorpion.NeoScorpionLiveAssignmentsTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.user = self.f._add_user('assessment_operator', 'operator')
        self.other = self.f._add_user('assessment_other', 'operator')
        db.session.commit()
        self.f._login(self.user)

    def test_no_sort_schema_is_additive_and_revision_is_read_only(self):
        result = {}
        for path in ('/neoscorpion/fuel-assignments/revision', '/neoscorpion/fuel-dispatch/revision'):
            r, sql = measured(self.f.client, path)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.headers['Cache-Control'], 'no-store')
            self.assertEqual((r.json['current_operation'], r.json['operation_id'], r.json['revision']), (False, None, 0))
            self.assertEqual(sql['writes'], 0)
            result[path] = dict(payload=r.json, sql=sql)
        observations['no_sort'] = result

    def test_real_denials_revocation_and_anonymous(self):
        paths = ('/neoscorpion/fuel-assignments/revision', '/neoscorpion/fuel-dispatch/revision', '/neoscorpion/fueler/live-panel', '/neoscorpion/fuel-dispatch/live-panel')
        statuses = {}
        for key in ('neoscorpion.fueler.view', 'neoscorpion.fuel_dispatch.view'):
            row = PermissionRule.query.filter_by(permission_key=key).one()
            row.minimum_role = 'grandmaster'
        db.session.commit()
        statuses['approved_node_read_policy'] = [fresh(self.f.client, p).status_code for p in paths]
        self.assertEqual(statuses['approved_node_read_policy'], [200] * 4)
        rejected = fresh(self.f.client, '/neoscorpion/fuel-dispatch/assignment', method='POST', data={})
        self.assertEqual(rejected.status_code, 403)
        statuses['operator_dispatch_mutation'] = 403
        for row in PermissionRule.query.filter(PermissionRule.permission_key.in_(('neoscorpion.fueler.view', 'neoscorpion.fuel_dispatch.view'))):
            row.minimum_role = 'watcher'
        node = NeoNode.query.filter_by(code='scorpion').one()
        membership = GatewayMembership.query.filter_by(user_id=self.user.id, gateway_id=self.f.gateway.id).one()
        grant = GatewayNodeRole.query.filter_by(gateway_membership_id=membership.id, node_id=node.id).one()
        grant.is_active = False
        db.session.commit()
        statuses['revoked_node'] = [fresh(self.f.client, p).status_code for p in paths]
        self.assertTrue(all((s in (302, 403) for s in statuses['revoked_node'])))
        self.assertFalse(db.session.get(GatewayNodeRole, grant.id).is_active)
        anonymous = self.f.app.test_client()
        statuses['anonymous'] = [fresh(anonymous, p).status_code for p in paths]
        self.assertEqual(statuses['anonymous'], [302] * 4)
        observations['authorization'] = statuses

    def test_current_sort_foreign_gateway_assignment_and_work_ownership(self):
        self.f._configure_active_night_sort()
        operation, mission = self.f._add_operation_with_mission()
        own = NeoScorpionFuelAssignment(sort_date_operation_id=operation.id, sort_date_mission_id=mission.id, assigned_fueler_user_id=self.user.id)
        db.session.add(own)
        db.session.flush()
        foreign = Gateway(code='TST', name='Synthetic isolation gateway')
        db.session.add(foreign)
        db.session.flush()
        other_op = SortDateOperation(gateway_id=foreign.id, gateway_code=foreign.code, sort_date=operation.sort_date, sort_name=operation.sort_name)
        stale_op = SortDateOperation(gateway_id=self.f.gateway.id, gateway_code=self.f.gateway.code, sort_date=date(2026, 8, 16), sort_name='night')
        db.session.add_all([other_op, stale_op])
        db.session.flush()
        ids = {}
        for key, op, actor in (('foreign_gateway', other_op, self.user), ('stale_sort', stale_op, self.user), ('foreign_fueler', operation, self.other)):
            m = SortDateMission(sort_date_operation_id=op.id, sort_date=op.sort_date, gateway_code=op.gateway_code, sort_name='night', mission_type='departure', mission_source='manual', flight_number='UPS-' + key, origin=op.gateway_code, destination='SDF', assigned_tail_number='N888UP')
            db.session.add(m)
            db.session.flush()
            a = NeoScorpionFuelAssignment(sort_date_operation_id=op.id, sort_date_mission_id=m.id, assigned_fueler_user_id=actor.id)
            db.session.add(a)
            db.session.flush()
            ids[key] = a.id
        db.session.commit()
        result = {}
        for key, aid in ids.items():
            r, sql = measured(self.f.client, f'/neoscorpion/fueler/assignments/{aid}')
            self.assertEqual(r.status_code, 404)
            self.assertEqual(sql['writes'], 0)
            result[key] = r.status_code
            r, sql = measured(self.f.client, f'/neoscorpion/fueler/assignments/{aid}/on', method='POST', data={})
            self.assertEqual(r.status_code, 400)
            expected_error = ('This fuel assignment belongs to another Fueler.' if key == 'foreign_fueler'
                else 'Fuel assignment was not found for the current sort operation during TRUCK ON.')
            self.assertEqual(r.json, {'ok': False, 'error': expected_error})
            self.assertEqual(sql['writes'], 0)
        r, sql = measured(self.f.client, '/neoscorpion/fuel-dispatch/revision?operation_id=' + str(other_op.id))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['operation_id'], operation.id)
        self.assertEqual(sql['writes'], 0)
        result['active_revision'] = dict(sql=sql, keys=sorted(r.json))
        for path in ('/neoscorpion/fuel-dispatch/live-panel', '/neoscorpion/fuel-dispatch/live-panel?completion_only=1&delta=1'):
            sep = '&' if '?' in path else '?'
            r = fresh(self.f.client, path + sep + 'operation_id=' + str(stale_op.id))
            self.assertEqual(r.status_code, 409)
        observations['isolation'] = result

    def test_revision_query_count_does_not_grow_with_fifty_missions(self):
        self.f._configure_active_night_sort()
        operation, mission = self.f._add_operation_with_mission()
        db.session.commit()
        r, one = measured(self.f.client, '/neoscorpion/fuel-dispatch/revision')
        self.assertEqual(r.status_code, 200)
        for i in range(49):
            db.session.add(SortDateMission(sort_date_operation_id=operation.id, sort_date=operation.sort_date, gateway_code=operation.gateway_code, sort_name=operation.sort_name, mission_type='departure', mission_source='manual', flight_number='UPS-SCALE' + str(i), origin=operation.gateway_code, destination='SDF', assigned_tail_number='N' + str(100 + i) + 'UP'))
        db.session.commit()
        r, fifty = measured(self.f.client, '/neoscorpion/fuel-dispatch/revision')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(one, fifty)
        self.assertEqual(one['writes'], 0)
        observations['revision_scale'] = dict(one_mission=one, fifty_missions=fifty)
