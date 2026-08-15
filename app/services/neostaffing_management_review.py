from collections import defaultdict
from datetime import date, datetime
import hashlib
import json

from app.extensions import db
from app.models import (
    StaffingLeadershipAssignment,
    StaffingPerson,
    StaffingReportingRelationship,
    StaffingUnit,
)
from app.services import neostaffing as staffing_service


ASSIGNMENT_UNIT_TYPE_BY_CLASSIFICATION = {
    "part_time_supervisor": "work_area",
    "full_time_specialist": ("department", "operation"),
    "full_time_supervisor": "department",
    "manager": "operation",
}


class ManagementRelationshipReviewBundle:
    def __init__(self, *, lock=False):
        self.units = _load_rows(
            StaffingUnit.query.order_by(StaffingUnit.id),
            lock=lock,
        )
        self.people = _load_rows(
            StaffingPerson.query.filter(
                StaffingPerson.classification.in_(
                    staffing_service.MANAGEMENT_CLASSIFICATIONS
                )
            ).order_by(StaffingPerson.id),
            lock=lock,
        )
        self.assignments = _load_rows(
            StaffingLeadershipAssignment.query.filter_by(active=True).order_by(
                StaffingLeadershipAssignment.id
            ),
            lock=lock,
        )
        self.relationships = _load_rows(
            StaffingReportingRelationship.query.filter_by(active=True).order_by(
                StaffingReportingRelationship.id
            ),
            lock=lock,
        )

        self.units_by_id = {row.id: row for row in self.units}
        self.people_by_id = {row.id: row for row in self.people}
        self.assignments_by_id = {row.id: row for row in self.assignments}
        self.assignments_by_person = defaultdict(list)
        self.assignments_by_unit = defaultdict(list)
        for assignment in self.assignments:
            self.assignments_by_person[assignment.person_id].append(assignment)
            self.assignments_by_unit[assignment.unit_id].append(assignment)

        self.relationship_by_person = {}
        for relationship in self.relationships:
            if relationship.person_id in self.relationship_by_person:
                raise ValueError(
                    "Multiple active Reports To records require configuration repair."
                )
            self.relationship_by_person[relationship.person_id] = relationship

    def active_candidates(self, classification):
        return sorted(
            (
                person
                for person in self.people
                if person.active and person.classification == classification
            ),
            key=_person_sort_key,
        )


def assignment_add_mutation(person_id, unit_id, leadership_level):
    return {
        "kind": "add_assignment",
        "person_id": _positive_int(person_id, "management person"),
        "unit_id": _positive_int(unit_id, "staffing unit"),
        "leadership_level": str(leadership_level or "").strip(),
    }


def assignment_remove_mutation(assignment_id):
    return {
        "kind": "remove_assignment",
        "assignment_id": _positive_int(assignment_id, "management assignment"),
    }


def unit_update_mutation(unit, normalized_values):
    return {
        "kind": "update_unit",
        "unit_id": int(unit.id),
        "unit_type": normalized_values["unit_type"],
        "name": normalized_values["name"],
        "parent_id": normalized_values["parent_id"],
        "display_order": int(normalized_values["display_order"]),
        "active": bool(normalized_values["active"]),
        "required_headcount": normalized_values["required_headcount"],
    }


def mutation_form_values(mutation):
    values = {}
    for key, value in mutation.items():
        if value is None:
            values[key] = ""
        elif isinstance(value, bool):
            values[key] = "1" if value else "0"
        else:
            values[key] = str(value)
    return values


def prepare_management_relationship_review(mutation, *, lock=False):
    mutation = _normalized_mutation(mutation)
    bundle = ManagementRelationshipReviewBundle(lock=lock)
    before_assignments = list(bundle.assignments)
    after_assignments = list(before_assignments)
    before_parent_overrides = {}
    after_parent_overrides = {}

    if mutation["kind"] == "add_assignment":
        person = bundle.people_by_id.get(mutation["person_id"])
        unit = bundle.units_by_id.get(mutation["unit_id"])
        if not person or not unit:
            raise ValueError("The selected management person or staffing unit was not found.")
        staffing_service.validate_leadership_assignment(
            person,
            unit,
            mutation["leadership_level"],
        )
        if any(
            row.person_id == person.id
            and row.unit_id == unit.id
            and row.leadership_level == mutation["leadership_level"]
            for row in before_assignments
        ):
            raise ValueError("This leadership assignment already exists.")
        after_assignments.append(
            _TransientAssignment(
                person_id=person.id,
                unit_id=unit.id,
                leadership_level=mutation["leadership_level"],
            )
        )
        affected_person_ids = [person.id]
        summary = (
            f"Assign {person.full_name} to "
            f"{_unit_path(unit.id, bundle.units_by_id)}."
        )
    elif mutation["kind"] == "remove_assignment":
        assignment = bundle.assignments_by_id.get(mutation["assignment_id"])
        if not assignment:
            raise ValueError("Management assignment changed while you were reviewing it.")
        person = bundle.people_by_id.get(assignment.person_id)
        unit = bundle.units_by_id.get(assignment.unit_id)
        after_assignments = [
            row for row in before_assignments if row.id != assignment.id
        ]
        affected_person_ids = [assignment.person_id]
        summary = (
            f"Remove {person.full_name if person else 'management person'} from "
            f"{_unit_path(unit.id, bundle.units_by_id) if unit else 'the selected unit'}."
        )
    else:
        unit = bundle.units_by_id.get(mutation["unit_id"])
        if not unit:
            raise ValueError("Staffing unit changed while you were reviewing it.")
        if unit.unit_type != mutation["unit_type"]:
            raise ValueError("Staffing unit type changed while you were reviewing it.")
        after_parent_overrides[unit.id] = mutation["parent_id"]
        expected_classification = _subject_classification_for_unit(unit.unit_type)
        affected_person_ids = sorted(
            {
                row.person_id
                for row in bundle.assignments_by_unit.get(unit.id, [])
                if bundle.people_by_id.get(row.person_id)
                and bundle.people_by_id[row.person_id].classification
                == expected_classification
            }
        )
        old_path = _unit_path(unit.id, bundle.units_by_id)
        new_path = _unit_path(
            unit.id,
            bundle.units_by_id,
            parent_overrides=after_parent_overrides,
            name_overrides={unit.id: mutation["name"]},
        )
        summary = f"Move {old_path} to {new_path}."

    rows = []
    for person_id in affected_person_ids:
        person = bundle.people_by_id.get(person_id)
        if not person or not person.active:
            continue
        before = _reporting_suggestions(
            person,
            before_assignments,
            bundle,
            parent_overrides=before_parent_overrides,
        )
        after = _reporting_suggestions(
            person,
            after_assignments,
            bundle,
            parent_overrides=after_parent_overrides,
        )
        relationship = bundle.relationship_by_person.get(person.id)
        current_target = (
            bundle.people_by_id.get(relationship.reports_to_person_id)
            if relationship
            else None
        )
        suggestions_changed = before["ids"] != after["ids"]
        needs_initial_assignment_review = bool(
            mutation["kind"] == "add_assignment"
            and not relationship
            and after["has_operational_scope"]
        )
        if not suggestions_changed and not needs_initial_assignment_review:
            continue
        valid_candidates = bundle.active_candidates(
            staffing_service.REPORTING_TARGET_CLASSIFICATION.get(
                person.classification
            )
        )
        suggested_people = [
            bundle.people_by_id[candidate_id]
            for candidate_id in after["ids"]
            if candidate_id in bundle.people_by_id
        ]
        rows.append(
            {
                "person": person,
                "current_relationship": relationship,
                "current_reports_to": current_target,
                "relationship_revision": staffing_service.reporting_relationship_revision(
                    relationship
                ),
                "suggested_people": suggested_people,
                "valid_candidates": valid_candidates,
                "ambiguous": after["ambiguous"],
            }
        )

    review = {
        "required": bool(rows),
        "consolidated": mutation["kind"] == "update_unit" and len(rows) > 1,
        "summary": summary,
        "mutation": mutation,
        "mutation_form": mutation_form_values(mutation),
        "rows": rows,
        "bundle": bundle,
    }
    review["revision"] = _review_revision(bundle, mutation)
    return review


def apply_management_relationship_review(
    mutation,
    expected_revision,
    decisions,
):
    review = prepare_management_relationship_review(mutation, lock=True)
    if not str(expected_revision or "").strip() or expected_revision != review["revision"]:
        raise ValueError(
            "Management assignments or Reports To changed while you were reviewing. "
            "Latest Org Chart data has been loaded."
        )
    if not review["required"]:
        raise ValueError(
            "This relationship review is no longer required. Latest Org Chart data has been loaded."
        )

    _apply_operational_mutation(review)
    relationship_changed = _apply_relationship_decisions(review, decisions)
    purged = staffing_service.purge_expired_reporting_relationship_history(date.today())
    db.session.flush()
    return {
        "review": review,
        "relationship_changed": relationship_changed,
        "purged": purged,
    }


def _apply_operational_mutation(review):
    mutation = review["mutation"]
    bundle = review["bundle"]
    if mutation["kind"] == "add_assignment":
        staffing_service.create_leadership_assignment(
            bundle.people_by_id[mutation["person_id"]],
            bundle.units_by_id[mutation["unit_id"]],
            mutation["leadership_level"],
        )
        return
    if mutation["kind"] == "remove_assignment":
        assignment = bundle.assignments_by_id.get(mutation["assignment_id"])
        if not assignment:
            raise ValueError("Management assignment changed while you were reviewing it.")
        staffing_service.delete_leadership_assignment(assignment)
        return

    unit = bundle.units_by_id.get(mutation["unit_id"])
    if not unit:
        raise ValueError("Staffing unit changed while you were reviewing it.")
    staffing_service.update_unit(unit, mutation)


def _apply_relationship_decisions(review, decisions):
    bundle = review["bundle"]
    changed = 0
    now = datetime.utcnow()
    as_of = date.today()
    for row in review["rows"]:
        person = row["person"]
        decision = decisions.get(person.id)
        if not decision:
            raise ValueError(f"Choose a Reports To decision for {person.full_name}.")
        if decision.get("expected_revision") != row["relationship_revision"]:
            raise ValueError(
                "Reports To changed while you were reviewing. Latest Org Chart data has been loaded."
            )
        action = decision.get("action")
        if action == "keep":
            continue
        if action != "change":
            raise ValueError(f"Choose a valid Reports To decision for {person.full_name}.")
        target_id = _positive_int(
            decision.get("reports_to_person_id"),
            "Reports To person",
        )
        valid_ids = {candidate.id for candidate in row["valid_candidates"]}
        if target_id not in valid_ids:
            raise ValueError(f"Choose a valid Reports To person for {person.full_name}.")
        target = bundle.people_by_id.get(target_id)
        staffing_service.validate_reporting_relationship(person, target)
        current = row["current_relationship"]
        if current and current.reports_to_person_id == target.id:
            continue
        if current:
            current.active = False
            current.effective_end = as_of
            current.updated_at = now
        relationship = StaffingReportingRelationship(
            person_id=person.id,
            reports_to_person_id=target.id,
            active=True,
            effective_start=as_of,
            effective_end=None,
            created_at=now,
            updated_at=now,
        )
        db.session.add(relationship)
        changed += 1
    return changed


def _reporting_suggestions(person, assignments, bundle, *, parent_overrides):
    target_classification = staffing_service.REPORTING_TARGET_CLASSIFICATION.get(
        person.classification
    )
    if not target_classification:
        return {
            "ids": tuple(),
            "ambiguous": False,
            "has_operational_scope": False,
        }
    valid_candidate_ids = {
        candidate.id for candidate in bundle.active_candidates(target_classification)
    }
    person_assignments = [
        assignment
        for assignment in assignments
        if assignment.person_id == person.id
        and _assignment_is_relevant(person.classification, assignment, bundle)
    ]
    candidate_sets = []
    for assignment in person_assignments:
        owner_unit_ids = _owner_unit_ids(
            person.classification,
            assignment.unit_id,
            bundle.units_by_id,
            parent_overrides,
        )
        owner_ids = {
            owner_assignment.person_id
            for owner_unit_id in owner_unit_ids
            for owner_assignment in bundle.assignments_by_unit.get(owner_unit_id, [])
            if owner_assignment.person_id in valid_candidate_ids
        }
        candidate_sets.append(frozenset(owner_ids or valid_candidate_ids))

    if not candidate_sets:
        suggested_ids = valid_candidate_ids
    else:
        suggested_ids = set().union(*candidate_sets)
    normalized_sets = {tuple(sorted(candidate_set)) for candidate_set in candidate_sets}
    return {
        "ids": tuple(sorted(suggested_ids)),
        "ambiguous": len(suggested_ids) > 1 or len(normalized_sets) > 1,
        "has_operational_scope": bool(person_assignments),
    }


def _owner_unit_ids(classification, unit_id, units_by_id, parent_overrides):
    unit = units_by_id.get(unit_id)
    if not unit:
        return set()
    parent_id = parent_overrides.get(unit.id, unit.parent_id)
    if classification == "part_time_supervisor":
        parent = units_by_id.get(parent_id)
        return {parent.id} if parent and parent.unit_type == "department" else set()
    if classification == "full_time_supervisor":
        parent = units_by_id.get(parent_id)
        return {parent.id} if parent and parent.unit_type == "operation" else set()
    if classification == "manager":
        parent = units_by_id.get(parent_id)
        return {parent.id} if parent and parent.unit_type == "sort" else set()
    if classification == "full_time_specialist":
        if unit.unit_type == "department":
            return {unit.id}
        if unit.unit_type == "operation":
            return {
                child.id
                for child in units_by_id.values()
                if child.unit_type == "department"
                and parent_overrides.get(child.id, child.parent_id) == unit.id
            }
    return set()


def _assignment_is_relevant(classification, assignment, bundle):
    expected = ASSIGNMENT_UNIT_TYPE_BY_CLASSIFICATION.get(classification)
    unit = bundle.units_by_id.get(assignment.unit_id)
    if not expected or not unit:
        return False
    if isinstance(expected, tuple):
        return unit.unit_type in expected
    return unit.unit_type == expected


def _subject_classification_for_unit(unit_type):
    return {
        "work_area": "part_time_supervisor",
        "department": "full_time_supervisor",
        "operation": "manager",
    }.get(unit_type)


def _review_revision(bundle, mutation):
    payload = {
        "mutation": mutation,
        "units": [
            [row.id, row.parent_id, row.unit_type, _timestamp(row.updated_at)]
            for row in bundle.units
        ],
        "people": [
            [row.id, row.classification, bool(row.active), _timestamp(row.updated_at)]
            for row in bundle.people
        ],
        "assignments": [
            [
                row.id,
                row.person_id,
                row.unit_id,
                row.leadership_level,
                _timestamp(row.updated_at),
            ]
            for row in bundle.assignments
        ],
        "relationships": [
            [
                row.id,
                row.person_id,
                row.reports_to_person_id,
                _timestamp(row.updated_at),
            ]
            for row in bundle.relationships
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _normalized_mutation(mutation):
    kind = str(mutation.get("kind") or "").strip()
    if kind == "add_assignment":
        return assignment_add_mutation(
            mutation.get("person_id"),
            mutation.get("unit_id"),
            mutation.get("leadership_level"),
        )
    if kind == "remove_assignment":
        return assignment_remove_mutation(mutation.get("assignment_id"))
    if kind != "update_unit":
        raise ValueError("Unsupported management relationship review.")
    return {
        "kind": kind,
        "unit_id": _positive_int(mutation.get("unit_id"), "staffing unit"),
        "unit_type": str(mutation.get("unit_type") or "").strip(),
        "name": str(mutation.get("name") or "").strip(),
        "parent_id": _optional_positive_int(mutation.get("parent_id")),
        "display_order": int(mutation.get("display_order") or 0),
        "active": _boolean(mutation.get("active")),
        "required_headcount": _optional_int(mutation.get("required_headcount")),
    }


def _unit_path(
    unit_id,
    units_by_id,
    *,
    parent_overrides=None,
    name_overrides=None,
):
    parent_overrides = parent_overrides or {}
    name_overrides = name_overrides or {}
    names = []
    visited = set()
    current = units_by_id.get(unit_id)
    while current and current.id not in visited:
        visited.add(current.id)
        names.append(name_overrides.get(current.id, current.name))
        current = units_by_id.get(
            parent_overrides.get(current.id, current.parent_id)
        )
    return " / ".join(reversed(names))


def _load_rows(query, *, lock):
    if lock:
        query = query.with_for_update()
    return query.all()


def _person_sort_key(person):
    return (
        person.last_name.lower(),
        person.first_name.lower(),
        person.employee_id.lower(),
        person.id,
    )


def _timestamp(value):
    return value.isoformat(timespec="microseconds") if value else ""


def _positive_int(value, label):
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Select a valid {label}.")
    if normalized <= 0:
        raise ValueError(f"Select a valid {label}.")
    return normalized


def _optional_positive_int(value):
    if value in (None, ""):
        return None
    return _positive_int(value, "parent unit")


def _optional_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError("Enter a valid number.")


def _boolean(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


class _TransientAssignment:
    id = None

    def __init__(self, *, person_id, unit_id, leadership_level):
        self.person_id = person_id
        self.unit_id = unit_id
        self.leadership_level = leadership_level
