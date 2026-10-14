"""Canonical append-only WorkState visits and deterministic current references."""
from app.extensions import db
from app.models import NeoScorpionFuelWorkState


def current_work(assignment, tail, *, lock=False):
    if assignment is None:
        return None
    query = NeoScorpionFuelWorkState.query.filter_by(fuel_assignment_id=assignment.id)
    if assignment.active_work_state_id is not None:
        query = query.filter_by(id=assignment.active_work_state_id)
    else:
        # Compatibility with unupgraded fixture/legacy rows only. Never choose
        # first/latest among physical visits, even if their tails match.
        query = query.filter_by(tail_number=tail, visit_number=None)
    if lock:
        query = query.with_for_update().populate_existing()
    rows = query.limit(2).all()
    if len(rows) > 1:
        raise ValueError("REVIEW REQUIRED: ambiguous legacy work; select its segment identity.")
    work = rows[0] if rows else None
    if assignment.active_work_state_id is not None and work is None:
        raise ValueError("REVIEW REQUIRED: current segment reference is invalid.")
    if work and (work.tail_number != tail or
            (work.physical_cycle_number is not None and work.physical_cycle_number != int(assignment.current_cycle_number or 1))):
        return None
    return work


def new_work(assignment, tail, user, now):
    # The caller holds the existing operation + assignment mutation locks.
    maximum = db.session.query(db.func.max(NeoScorpionFuelWorkState.visit_number)).filter_by(
        fuel_assignment_id=assignment.id).scalar() or 0
    work = NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id, tail_number=tail,
        visit_number=maximum+1, physical_sequence_number=maximum+1,
        physical_cycle_number=int(assignment.current_cycle_number or 1),
        physical_cycle_type=assignment.current_cycle_type or "fuel",
        physical_fueler_user_id=assignment.assigned_fueler_user_id,
        physical_truck_id=assignment.assigned_truck_id,
        created_at=now, updated_at=now)
    db.session.add(work); db.session.flush()
    assignment.active_work_state_id = work.id
    assignment.transfer_fuel_gallons = None  # Compatible projection, never source.
    return work


class WorkIndex(dict):
    def __init__(self, assignments, works):
        super().__init__()
        self.all_segments = tuple(works)
        by_id = {work.id: work for work in works}
        by_owner = {}
        for work in works:
            by_owner.setdefault(work.fuel_assignment_id, []).append(work)
        for assignment in assignments:
            if assignment.active_work_state_id is not None:
                work = by_id.get(assignment.active_work_state_id)
                if work is None or work.fuel_assignment_id != assignment.id:
                    raise ValueError("REVIEW REQUIRED: invalid current physical segment.")
            else:
                tail = (assignment.sort_date_mission.assigned_tail_number or "").strip().upper()
                candidates = [item for item in by_owner.get(assignment.id, ())
                    if item.visit_number is None and item.tail_number == tail]
                work = candidates[0] if len(candidates) == 1 else None
            if work:
                self[(assignment.id, work.tail_number)] = work


def all_work(index):
    return getattr(index, "all_segments", tuple({work.id: work for work in index.values()}.values()))
