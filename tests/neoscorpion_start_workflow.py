"""R03I synthetic planning plus authenticated assignment and canonical ON/OFF."""
from datetime import timedelta

from app.extensions import db
from app.models import NeoScorpionSortFueler, NeoScorpionFuelWorkState, SortDateParkingAssignment
from app.services import neoscorpion as fuel
from tests.neoscorpion_reactivation_workflow import assign_resources


def plan_other(case, *, flight='PREVIOUS', tail='N492UP', at=None, position='A04'):
    mission=case.fixture._mission(flight,tail,52000,0)
    assignment=case.fixture._assignment(mission)
    if position is not None:
        db.session.add(SortDateParkingAssignment(sort_date_operation_id=case.operation.id,
            tail_number=tail,ramp_code='A',position_code=position))
    if not NeoScorpionSortFueler.query.filter_by(sort_date_operation_id=case.operation.id,user_id=case.fueler.id).first():
        db.session.add(NeoScorpionSortFueler(sort_date_operation_id=case.operation.id,user_id=case.fueler.id))
    db.session.commit()
    assign_resources(case,assignment,case.truck,case.fueler,at=at or case.now-timedelta(minutes=40))
    return assignment


def previous_physical(case, *, position='A04'):
    assignment=plan_other(case,position=position)
    fuel.confirm_fueler_truck_on(case.gateway,case.fueler,assignment.id,
        expected_cycle=1,expected_tail=assignment.sort_date_mission.assigned_tail_number,
        expected_truck_id=case.truck.id,now_utc=case.now-timedelta(minutes=30))
    db.session.commit()
    return assignment,db.session.get(NeoScorpionFuelWorkState,assignment.active_work_state_id)


def release(case,assignment,*,at=None):
    fuel.mark_fueler_off(case.gateway,case.fueler,assignment.id,now_utc=at or case.now)
    db.session.commit()
