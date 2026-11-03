"""Synthetic resources, authenticated assignment and real ON for R03G fixtures."""
from datetime import datetime,timedelta
from copy import deepcopy
from unittest.mock import patch
from app.extensions import db
from app.models import NeoScorpionFuelWorkState,NeoScorpionSortFueler,NeoScorpionSortTruck
from app.services import neoscorpion as fuel


def assign_resources(case,assignment,truck,fueler=None,*,at=None):
    # The HTTP client owns another transaction. Publish synthetic planning
    # records first; their PostgreSQL FK locks must not block its operation lock.
    db.session.commit()
    class AssignmentClock(datetime):
        @classmethod
        def utcnow(cls):return at or datetime.utcnow()
    email=case.dispatcher.email
    data=dict(mission_id=assignment.sort_date_mission_id,
        expected_assigned_fueler_user_id=assignment.assigned_fueler_user_id or '',
        expected_assigned_truck_id=assignment.assigned_truck_id or '',
        assigned_fueler_user_id=fueler.id if fueler else '',assigned_truck_id=truck.id)
    with case.fixture.app.app_context():
        client=case.fixture.app.test_client()
        response=client.post('/login',data={'email':email,'password':'TestPassword123!'})
        case.assertEqual(response.status_code,302);case.assertNotIn('/login',response.location)
        with patch.object(fuel,'datetime',AssignmentClock):
            response=client.post('/neoscorpion/fuel-dispatch/assignment',data=data)
        case.assertEqual(response.status_code,302);case.assertNotIn('/login',response.location)
    db.session.refresh(assignment)
    case.assertEqual(assignment.assigned_truck_id,truck.id)
    case.assertEqual(assignment.assigned_fueler_user_id,fueler.id if fueler else None)


def event_snapshot(event):
    return deepcopy(({c.name:getattr(event,c.name) for c in event.__table__.columns},
        [{c.name:getattr(tank,c.name) for c in tank.__table__.columns}
         for tank in sorted(event.tank_snapshots,key=lambda row:row.tank_code)]))


def prepare(case,lead=15):
    if hasattr(case,'work'):return
    case.start=datetime.utcnow().replace(microsecond=0)-timedelta(minutes=30)
    case.truck=case.fixture._nightly_truck()
    case.selection=NeoScorpionSortTruck.query.filter_by(fuel_truck_id=case.truck.id).one()
    case.settings.assignment_setup_minutes=8;case.settings.advance_setup_window_minutes=10
    db.session.add(NeoScorpionSortFueler(sort_date_operation_id=case.operation.id,user_id=case.fueler.id))
    db.session.commit()
    assign_resources(case,case.assignment,case.truck,case.fueler,at=case.start-timedelta(minutes=5+lead))
    fuel.confirm_fueler_truck_on(case.gateway,case.fueler,case.assignment.id,
        expected_cycle=1,expected_tail=case.mission.assigned_tail_number,expected_truck_id=case.truck.id,now_utc=case.start)
    db.session.commit()
    case.work=db.session.get(NeoScorpionFuelWorkState,case.assignment.active_work_state_id)
