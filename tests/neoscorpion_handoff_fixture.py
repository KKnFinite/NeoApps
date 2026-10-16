"""Explicit handoff for older inventory/report fixtures, never physical actions.

These tests predate mandatory Fueler submission. Preparing a verified event must
exercise that real stage, not fake its columns or relax production validation.
"""
from app.extensions import db
from app.models import NeoScorpionFuelAssignment, NeoScorpionFuelWorkState, User
from app.services import neoscorpion as fuel


def submit_and_verify(gateway, dispatcher, assignment_id, **kwargs):
    assignment=db.session.get(NeoScorpionFuelAssignment,assignment_id)
    identity={key:kwargs[key] for key in ('physical_work_id','physical_sequence') if kwargs.get(key) is not None}
    work=db.session.get(NeoScorpionFuelWorkState,identity.get('physical_work_id') or assignment.active_work_state_id) if assignment else None
    if work and not work.physical_finalized_at_utc and (identity or not assignment.completed_at_utc):
        owner=db.session.get(User,work.physical_fueler_user_id or assignment.assigned_fueler_user_id)
        rows=fuel.fueler_context(gateway,owner,assignment_id=assignment_id,include_timing=False,**identity)['rows']
        row=next(row for row in rows if row['fuel_work_state'].id==work.id)
        fuel.submit_fueler_assignment(gateway,owner,assignment_id,expected=row['edit_baseline'],**identity)
    return fuel.complete_fueled_assignment(gateway,dispatcher,assignment_id,**kwargs)
