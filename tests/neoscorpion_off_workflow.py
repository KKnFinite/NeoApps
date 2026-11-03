"""Canonical synthetic assignment/ON helpers shared only by R03F tests."""
from datetime import datetime

from app.extensions import db
from app.models import NeoScorpionFuelTruck, NeoScorpionSortTruck, NeoScorpionSortFueler
from app.services import neoscorpion as fuel


def assign_truck(case, assignment, fueler, dispatcher, *, start=True):
    operation = assignment.sort_date_operation
    truck = NeoScorpionFuelTruck(gateway_id=case.gateway.id,
        truck_number=f'OFF-{assignment.id}', capacity_gallons=2000)
    db.session.add(truck); db.session.flush()
    selection = NeoScorpionSortTruck(sort_date_operation_id=operation.id,
        fuel_truck_id=truck.id, status='available', starting_gallons=1000, current_gallons=1000)
    db.session.add(selection)
    if not NeoScorpionSortFueler.query.filter_by(sort_date_operation_id=operation.id,user_id=fueler.id).first():
        db.session.add(NeoScorpionSortFueler(sort_date_operation_id=operation.id,user_id=fueler.id))
    db.session.commit()
    # A real authenticated Dispatcher action establishes resource authority;
    # no test sets physical ON/OFF/submission/completion fields directly.
    email=dispatcher.email
    form=dict(
        mission_id=assignment.sort_date_mission_id, expected_assigned_fueler_user_id=fueler.id,
        expected_assigned_truck_id='', assigned_fueler_user_id=fueler.id, assigned_truck_id=truck.id)
    # The unittest holds an outer DB app context. Give this separate browser
    # client its own request globals, as production requests receive, so Flask
    # Login's g cache cannot leak between the fixture's independent clients.
    with case.app.app_context():
        client = case.app.test_client()
        response = client.post('/login',data={'email':email,'password':'TestPassword123!'})
        case.assertEqual(response.status_code,302)
        case.assertNotIn('/login',response.location)
        response = client.post('/neoscorpion/fuel-dispatch/assignment',data=form)
        case.assertEqual(response.status_code,302)
        case.assertNotIn('/login',response.location)
    db.session.refresh(assignment)
    case.assertEqual(assignment.assigned_truck_id,truck.id)
    if start:
        fuel.confirm_fueler_truck_on(case.gateway,fueler,assignment.id,expected_cycle=1,
            expected_tail=assignment.sort_date_mission.assigned_tail_number,expected_truck_id=truck.id,
            now_utc=datetime(2026,8,18,3,30))
        db.session.commit()
    return truck,selection


def revision(operation):
    from app.models import NeoScorpionSortAssetState
    state=NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=operation.id).first()
    return int(state.revision if state else 0)


def submit(case, assignment, user, *, now=datetime(2026,8,18,4,15)):
    row=fuel.fueler_context(case.gateway,user,assignment_id=assignment.id,include_timing=False)['rows'][0]
    result=fuel.submit_fueler_assignment(case.gateway,user,assignment.id,
        expected=row['edit_baseline'],now_utc=now)
    db.session.commit()
    return result
