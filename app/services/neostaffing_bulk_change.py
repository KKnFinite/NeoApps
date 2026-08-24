from collections import defaultdict
from datetime import date, datetime
import hashlib
import json
import uuid

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.extensions import db
from app.models import (
    StaffingLeadershipAssignment,
    StaffingPerson,
    StaffingReportingRelationship,
    StaffingUnit,
    StaffingWorkAssignment,
)
from app.models.staffing_person import (
    STAFFING_CLASSIFICATIONS,
    STAFFING_DATABASE_CLASSIFICATIONS,
    STAFFING_EMPLOYEE_STATUSES,
)
from app.models.user import ROLE_LEVELS
from app.services.access_control import get_user_app_role
from app.services import neostaffing as staffing_service
from app.services import neostaffing_change_requests as change_request_service
from app.services.permission_rules import user_can


BULK_CHANGE_PERMISSION = "neostaffing.bulk_change.use"
PEOPLE_EDIT_PERMISSION = "neostaffing.people.edit"
MANAGEMENT_ASSIGN_PERMISSION = "neostaffing.management.assign"
ORG_CHART_EDIT_STRUCTURE_PERMISSION = "neostaffing.org_chart.edit_structure"

WORKSPACE_VERSION = 1
WORKSPACE_MAX_AGE_SECONDS = 12 * 60 * 60
WORKSPACE_SALT = "neostaffing-bulk-change-v1"
LIVE_DATA_CHANGED_MESSAGE = "Live Data Changed - Review Required"

PERSON_FIELDS = (
    "employee_id",
    "first_name",
    "last_name",
    "seniority_date",
    "phone_number",
    "classification",
    "employee_status",
    "active",
    "work_area_unit_id",
)
REQUEST_SUPPORTED_FIELDS = {
    "first_name",
    "last_name",
    "seniority_date",
    "employee_status",
    "classification",
    "work_area_unit_id",
}

ASSIGNMENT_UNIT_TYPES = {
    "part_time_supervisor": {"work_area"},
    "full_time_specialist": {"department", "operation"},
    "full_time_supervisor": {"department"},
    "manager": {"operation"},
    "division_manager": {"sort"},
}


class BulkChangeDataBundle:
    """Bounded, request-local snapshot used for staging and final validation."""

    def __init__(self, *, lock=False):
        self.units = _load_rows(StaffingUnit.query.order_by(StaffingUnit.id), lock)
        self.people = _load_rows(StaffingPerson.query.order_by(StaffingPerson.id), lock)
        self.work_assignments = _load_rows(
            StaffingWorkAssignment.query.order_by(StaffingWorkAssignment.id),
            lock,
        )
        self.leadership_assignments = _load_rows(
            StaffingLeadershipAssignment.query.order_by(
                StaffingLeadershipAssignment.id
            ),
            lock,
        )
        self.reporting_relationships = _load_rows(
            StaffingReportingRelationship.query.filter_by(active=True).order_by(
                StaffingReportingRelationship.id
            ),
            lock,
        )

        self.units_by_id = {row.id: row for row in self.units}
        self.people_by_id = {row.id: row for row in self.people}
        self.people_by_employee_id = {
            row.employee_id.strip().lower(): row for row in self.people
        }
        self.work_by_person = {row.person_id: row for row in self.work_assignments}
        self.leadership_by_id = {
            row.id: row for row in self.leadership_assignments
        }
        self.leadership_by_person = defaultdict(list)
        self.leadership_by_unit = defaultdict(list)
        for row in self.leadership_assignments:
            if not row.active:
                continue
            self.leadership_by_person[row.person_id].append(row)
            self.leadership_by_unit[row.unit_id].append(row)
        self.relationship_by_person = {}
        for row in self.reporting_relationships:
            if row.person_id in self.relationship_by_person:
                raise ValueError(
                    "Multiple active Reports To records require configuration repair."
                )
            self.relationship_by_person[row.person_id] = row
        self.children_by_parent = defaultdict(list)
        for row in self.units:
            self.children_by_parent[row.parent_id].append(row)
        self.revision = _bundle_revision(self)

    def person_for_user(self, user):
        employee_id = str(getattr(user, "employee_id", "") or "").strip().lower()
        return self.people_by_employee_id.get(employee_id)

    def unit_path(self, unit_id, parent_overrides=None):
        parent_overrides = parent_overrides or {}
        names = []
        visited = set()
        unit = self.units_by_id.get(unit_id)
        while unit and unit.id not in visited:
            visited.add(unit.id)
            names.append(unit.name)
            unit = self.units_by_id.get(
                parent_overrides.get(unit.id, unit.parent_id)
            )
        return " / ".join(reversed(names))


def new_workspace(user):
    return {
        "version": WORKSPACE_VERSION,
        "owner_user_id": int(user.id),
        "base_revision": None,
        "people": {},
        "leadership_add": [],
        "leadership_remove": [],
        "reporting": {},
        "units": {},
    }


def encode_workspace(workspace):
    return _serializer().dumps(workspace)


def decode_workspace(token, user):
    if not token:
        return new_workspace(user)
    try:
        workspace = _serializer().loads(
            token,
            max_age=WORKSPACE_MAX_AGE_SECONDS,
        )
    except SignatureExpired as error:
        raise ValueError("This Bulk Change workspace expired. Start a new workspace.") from error
    except BadSignature as error:
        raise ValueError("This Bulk Change workspace is invalid. Start a new workspace.") from error
    if (
        workspace.get("version") != WORKSPACE_VERSION
        or workspace.get("owner_user_id") != int(user.id)
    ):
        raise ValueError("This Bulk Change workspace belongs to another session.")
    return workspace


def stage_workspace_change(workspace, action, values, user):
    bundle = BulkChangeDataBundle()
    _require_bulk_access(user)
    if action == "review_latest":
        workspace["base_revision"] = bundle.revision
        return workspace

    _ensure_workspace_revision(workspace, bundle)
    if action == "stage_person":
        _stage_existing_person(workspace, values, bundle, user)
    elif action == "stage_new_person":
        _stage_new_person(workspace, values, bundle, user)
    elif action == "stage_leadership_add":
        _stage_leadership_add(workspace, values, bundle, user)
    elif action == "stage_leadership_remove":
        _stage_leadership_remove(workspace, values, bundle, user)
    elif action == "stage_reporting":
        _stage_reporting(workspace, values, bundle, user)
    elif action == "stage_unit":
        _stage_unit(workspace, values, bundle, user)
    elif action == "remove_change":
        _remove_change(workspace, values)
    else:
        raise ValueError("Choose a valid Bulk Change action.")

    return workspace


def bulk_change_context(workspace, user, *, bundle=None):
    bundle = bundle or BulkChangeDataBundle()
    actor = _actor_context(user, bundle)
    simulation = _simulate(workspace, bundle)
    staged_groups = _staged_groups(workspace, simulation, bundle)
    active_people = sorted(
        (row for row in bundle.people if row.active),
        key=_person_sort_key,
    )
    management_states = sorted(
        (
            state
            for state in simulation["states"].values()
            if state["active"]
            and state["classification"] in staffing_service.MANAGEMENT_CLASSIFICATIONS
        ),
        key=lambda state: (
            state["last_name"].lower(),
            state["first_name"].lower(),
            state["employee_id"].lower(),
            state["ref"],
        ),
    )
    return {
        "workspace": workspace,
        "bundle": bundle,
        "actor": actor,
        "mode": "submit" if actor["is_pt_supervisor"] and not actor["is_grandmaster"] else "apply",
        "people": active_people,
        "management_states": management_states,
        "units": sorted(
            (row for row in bundle.units if row.active),
            key=lambda row: (row.unit_type, row.display_order, row.name.lower(), row.id),
        ),
        "work_areas": sorted(
            (row for row in bundle.units if row.active and row.unit_type == "work_area"),
            key=lambda row: (bundle.unit_path(row.id).lower(), row.id),
        ),
        "active_leadership": sorted(
            (
                row
                for row in bundle.leadership_assignments
                if row.active
            ),
            key=lambda row: (row.person_id, row.unit_id, row.id),
        ),
        "staged_groups": staged_groups,
        "relationship_reviews": simulation["relationship_reviews"],
        "blocking_errors": simulation["errors"],
        "unsupported_items": _submission_unsupported_items(workspace, simulation, bundle),
        "has_changes": _workspace_has_changes(workspace),
        "classification_choices": staffing_service.classification_choices(),
        "employee_status_choices": staffing_service.employee_status_choices(),
        "classification_labels": staffing_service.CLASSIFICATION_LABELS,
        "unit_type_labels": staffing_service.UNIT_TYPE_LABELS,
        "leadership_level_labels": staffing_service.LEADERSHIP_LEVEL_LABELS,
    }


def apply_workspace(workspace, user):
    _require_bulk_access(user)
    bundle = BulkChangeDataBundle(lock=True)
    _require_current_revision(workspace, bundle)
    actor = _actor_context(user, bundle)
    if actor["is_pt_supervisor"] and not actor["is_grandmaster"]:
        raise ValueError("PT Supervisors must submit supported employee changes for approval.")
    simulation = _simulate(workspace, bundle)
    if simulation["errors"]:
        raise ValueError(simulation["errors"][0])
    _validate_workspace_scope(workspace, simulation, actor, bundle)
    _validate_direct_authority(workspace, simulation, actor, user)

    now = datetime.utcnow()
    today = date.today()
    ref_to_person = {
        f"p:{row.id}": row for row in bundle.people
    }
    new_states = [
        state for state in simulation["states"].values() if state["is_new"]
    ]
    for state in new_states:
        person = StaffingPerson(
            employee_id=state["employee_id"],
            first_name=state["first_name"],
            last_name=state["last_name"],
            seniority_date=date.fromisoformat(state["seniority_date"]),
            phone_number=state["phone_number"],
            classification=state["classification"],
            employee_status=state["employee_status"],
            active=state["active"],
            created_at=now,
            updated_at=now,
        )
        db.session.add(person)
        ref_to_person[state["ref"]] = person
    if new_states:
        db.session.flush()

    changed_people = 0
    for ref, staged in workspace["people"].items():
        state = simulation["states"][ref]
        person = ref_to_person[ref]
        if not state["is_new"]:
            for field in (
                "employee_id",
                "first_name",
                "last_name",
                "phone_number",
                "classification",
                "employee_status",
                "active",
            ):
                setattr(person, field, state[field])
            person.seniority_date = date.fromisoformat(state["seniority_date"])
            person.updated_at = now
        changed_people += 1

    for unit_id_text, change in workspace["units"].items():
        unit = bundle.units_by_id[int(unit_id_text)]
        unit.parent_id = change["parent_id"]
        unit.updated_at = now

    work_by_person_id = dict(bundle.work_by_person)
    for ref in simulation["work_touched_refs"]:
        person = ref_to_person[ref]
        state = simulation["states"][ref]
        assignment = work_by_person_id.get(person.id)
        target_id = state["work_area_unit_id"]
        if target_id is None:
            if assignment and assignment.active:
                assignment.active = False
                assignment.updated_at = now
            continue
        if assignment:
            assignment.work_area_unit_id = target_id
            assignment.active = True
            assignment.updated_at = now
        else:
            assignment = StaffingWorkAssignment(
                person_id=person.id,
                work_area_unit_id=target_id,
                active=True,
                created_at=now,
                updated_at=now,
            )
            db.session.add(assignment)
            work_by_person_id[person.id] = assignment

    leadership_by_key = {
        (row.person_id, row.unit_id, row.leadership_level): row
        for row in bundle.leadership_assignments
    }
    for ref in simulation["leadership_touched_refs"]:
        person = ref_to_person[ref]
        desired = {
            (person.id, row["unit_id"], row["leadership_level"])
            for row in simulation["states"][ref]["leadership"]
        }
        current_keys = {
            key
            for key, row in leadership_by_key.items()
            if key[0] == person.id and row.active
        }
        for key in current_keys - desired:
            leadership_by_key[key].active = False
            leadership_by_key[key].updated_at = now
        for key in desired - current_keys:
            row = leadership_by_key.get(key)
            if row:
                row.active = True
                row.updated_at = now
            else:
                row = StaffingLeadershipAssignment(
                    person_id=key[0],
                    unit_id=key[1],
                    leadership_level=key[2],
                    active=True,
                    created_at=now,
                    updated_at=now,
                )
                db.session.add(row)
                leadership_by_key[key] = row

    for ref in simulation["relationship_touched_refs"]:
        person = ref_to_person[ref]
        target_ref = simulation["states"][ref]["reports_to_ref"]
        target = ref_to_person.get(target_ref) if target_ref else None
        current = bundle.relationship_by_person.get(person.id)
        if current and target and current.reports_to_person_id == target.id:
            continue
        if current:
            current.active = False
            current.effective_end = today
            current.updated_at = now
        if target:
            db.session.add(
                StaffingReportingRelationship(
                    person_id=person.id,
                    reports_to_person_id=target.id,
                    active=True,
                    effective_start=today,
                    effective_end=None,
                    created_at=now,
                    updated_at=now,
                )
            )

    purged = staffing_service.purge_expired_reporting_relationship_history(today)
    db.session.flush()
    return {
        "people": changed_people,
        "unit_changes": len(workspace["units"]),
        "leadership_changes": len(workspace["leadership_add"])
        + len(workspace["leadership_remove"]),
        "reporting_changes": len(workspace["reporting"]),
        "purged_reporting_history": purged,
    }


def submit_workspace(workspace, user):
    _require_bulk_access(user)
    bundle = BulkChangeDataBundle()
    _require_current_revision(workspace, bundle)
    actor = _actor_context(user, bundle)
    if not actor["is_pt_supervisor"] or actor["is_grandmaster"]:
        raise ValueError("This Bulk Change workspace uses Apply Changes, not approval submission.")
    simulation = _simulate(workspace, bundle)
    packages = []
    unsupported = _submission_unsupported_items(workspace, simulation, bundle)
    unsupported_keys = {
        (row.get("person_ref"), row.get("field")) for row in unsupported
    }
    for ref, staged in workspace["people"].items():
        if staged.get("kind") != "existing":
            continue
        state = simulation["states"].get(ref)
        person = bundle.people_by_id.get(staged.get("person_id"))
        if not state or not person or person.classification not in staffing_service.NON_MANAGEMENT_CLASSIFICATIONS:
            continue
        changes = {}
        for field, value in staged.get("changes", {}).items():
            if field not in REQUEST_SUPPORTED_FIELDS:
                continue
            if (ref, field) in unsupported_keys:
                continue
            changes[field] = value
        if changes:
            packages.append(
                {
                    "person_id": person.id,
                    "changes": changes,
                    "request_note": staged.get("request_note"),
                }
            )

    result = change_request_service.submit_bulk_change_requests(packages, user)
    submitted_by_person = defaultdict(set)
    for row in result["submitted_fields"]:
        submitted_by_person[f"p:{row['person_id']}"].add(row["field"])
    for ref, fields in submitted_by_person.items():
        staged = workspace["people"].get(ref)
        if not staged:
            continue
        for field in fields:
            staged["changes"].pop(field, None)
        if not staged["changes"]:
            workspace["people"].pop(ref, None)
    result["unsupported"] = unsupported
    workspace["base_revision"] = None
    return result


def _simulate(workspace, bundle):
    errors = []
    states = {}
    for person in bundle.people:
        assignment = bundle.work_by_person.get(person.id)
        relationship = bundle.relationship_by_person.get(person.id)
        states[f"p:{person.id}"] = {
            "ref": f"p:{person.id}",
            "is_new": False,
            "person_id": person.id,
            "employee_id": person.employee_id,
            "first_name": person.first_name,
            "last_name": person.last_name,
            "seniority_date": person.seniority_date.isoformat(),
            "phone_number": person.phone_number,
            "classification": person.classification,
            "employee_status": person.employee_status,
            "active": bool(person.active),
            "work_area_unit_id": (
                assignment.work_area_unit_id if assignment and assignment.active else None
            ),
            "leadership": [
                {
                    "assignment_id": row.id,
                    "unit_id": row.unit_id,
                    "leadership_level": row.leadership_level,
                }
                for row in bundle.leadership_by_person.get(person.id, [])
            ],
            "reports_to_ref": (
                f"p:{relationship.reports_to_person_id}" if relationship else None
            ),
        }

    person_field_touched = set()
    work_touched = set()
    leadership_touched = set()
    relationship_touched = set()
    management_touched = set()
    for ref, staged in workspace["people"].items():
        if staged.get("kind") == "new":
            values = staged.get("values", {})
            state = {
                "ref": ref,
                "is_new": True,
                "person_id": None,
                **values,
                "leadership": [],
                "reports_to_ref": None,
            }
            states[ref] = state
            person_field_touched.add(ref)
            work_touched.add(ref)
            if state["classification"] in staffing_service.MANAGEMENT_CLASSIFICATIONS:
                management_touched.add(ref)
        else:
            state = states.get(ref)
            if not state:
                errors.append("A staged employee no longer exists.")
                continue
            changes = staged.get("changes", {})
            old_classification = state["classification"]
            for field, value in changes.items():
                if field in PERSON_FIELDS:
                    state[field] = value
            person_field_touched.add(ref)
            if "work_area_unit_id" in changes:
                work_touched.add(ref)
            if "classification" in changes:
                management_touched.add(ref)
                leadership_touched.add(ref)
                relationship_touched.add(ref)
                if old_classification != state["classification"]:
                    for other_ref, other_state in states.items():
                        if other_state["reports_to_ref"] == ref:
                            management_touched.add(other_ref)
                            relationship_touched.add(other_ref)
            if "active" in changes:
                management_touched.add(ref)
                relationship_touched.add(ref)
                if not state["active"]:
                    for other_ref, other_state in states.items():
                        if other_state["reports_to_ref"] == ref:
                            management_touched.add(other_ref)
                            relationship_touched.add(other_ref)

    parent_overrides = {
        int(unit_id): change["parent_id"]
        for unit_id, change in workspace["units"].items()
    }
    errors.extend(_validate_parent_overrides(bundle, parent_overrides))

    for state in states.values():
        if state["classification"] not in staffing_service.NON_MANAGEMENT_CLASSIFICATIONS:
            if state["work_area_unit_id"] is not None:
                state["work_area_unit_id"] = None
                work_touched.add(state["ref"])
        if (
            not state["active"]
            or state["classification"] == "division_manager"
            or state["classification"]
            not in staffing_service.REPORTING_TARGET_CLASSIFICATION
        ):
            if state["reports_to_ref"] is not None:
                state["reports_to_ref"] = None
        state["leadership"] = [
            row
            for row in state["leadership"]
            if _leadership_is_valid(
                state["classification"],
                bundle.units_by_id.get(row["unit_id"]),
                row["leadership_level"],
            )
        ]

    remove_ids = {int(value) for value in workspace["leadership_remove"]}
    for assignment_id in remove_ids:
        assignment = bundle.leadership_by_id.get(assignment_id)
        if not assignment or not assignment.active:
            errors.append("A staged management assignment no longer exists.")
            continue
        ref = f"p:{assignment.person_id}"
        state = states.get(ref)
        if state:
            state["leadership"] = [
                row
                for row in state["leadership"]
                if row.get("assignment_id") != assignment_id
            ]
            leadership_touched.add(ref)
            management_touched.add(ref)

    for row in workspace["leadership_add"]:
        ref = row["person_ref"]
        state = states.get(ref)
        unit = bundle.units_by_id.get(row["unit_id"])
        if not state or not unit:
            errors.append("A staged management person or unit no longer exists.")
            continue
        if not _leadership_is_valid(
            state["classification"], unit, row["leadership_level"]
        ):
            errors.append(
                f"{state['first_name']} {state['last_name']} cannot lead {unit.name}."
            )
            continue
        key = (row["unit_id"], row["leadership_level"])
        if key not in {
            (current["unit_id"], current["leadership_level"])
            for current in state["leadership"]
        }:
            state["leadership"].append(
                {
                    "assignment_id": None,
                    "unit_id": row["unit_id"],
                    "leadership_level": row["leadership_level"],
                }
            )
        leadership_touched.add(ref)
        management_touched.add(ref)

    for ref, decision in workspace["reporting"].items():
        state = states.get(ref)
        if not state:
            errors.append("A staged Reports To person no longer exists.")
            continue
        action = decision.get("action")
        if action == "change":
            state["reports_to_ref"] = decision.get("target_ref")
        elif action == "clear":
            state["reports_to_ref"] = None
        elif action != "keep":
            errors.append("Choose a valid Reports To decision.")
        relationship_touched.add(ref)
        management_touched.add(ref)

    errors.extend(_validate_person_states(states, bundle))
    before_states = _baseline_states(bundle)
    before_suggestions = _all_suggestions(before_states, bundle, {})
    after_suggestions = _all_suggestions(states, bundle, parent_overrides)
    relationship_reviews = []
    all_refs = set(before_suggestions) | set(after_suggestions) | management_touched
    for ref in sorted(all_refs):
        state = states.get(ref)
        if not state or not state["active"]:
            continue
        classification = state["classification"]
        target_classification = staffing_service.REPORTING_TARGET_CLASSIFICATION.get(
            classification
        )
        if not target_classification:
            if classification == "division_manager" and state["reports_to_ref"]:
                errors.append("Division Managers cannot have a Reports To assignment.")
            continue
        suggestions_changed = before_suggestions.get(ref, ()) != after_suggestions.get(ref, ())
        explicit = workspace["reporting"].get(ref)
        current_target_ref = _baseline_reports_to_ref(ref, bundle)
        current_target_valid = _target_is_valid(
            state,
            states.get(current_target_ref),
        )
        final_target_valid = _target_is_valid(
            state,
            states.get(state["reports_to_ref"]),
        )
        needs_review = bool(
            suggestions_changed
            or explicit
            or (ref in management_touched and not current_target_valid)
        )
        if not needs_review:
            continue
        valid_candidates = sorted(
            (
                candidate
                for candidate in states.values()
                if candidate["active"]
                and candidate["classification"] == target_classification
                and candidate["ref"] != ref
            ),
            key=lambda candidate: (
                candidate["last_name"].lower(),
                candidate["first_name"].lower(),
                candidate["employee_id"].lower(),
                candidate["ref"],
            ),
        )
        resolved = bool(explicit and final_target_valid)
        if explicit and explicit.get("action") == "keep":
            resolved = current_target_valid
        if not resolved:
            errors.append(
                f"Choose a Reports To decision for {state['first_name']} {state['last_name']}."
            )
        relationship_reviews.append(
            {
                "person": state,
                "current": states.get(current_target_ref),
                "final": states.get(state["reports_to_ref"]),
                "suggested": [
                    states[candidate_ref]
                    for candidate_ref in after_suggestions.get(ref, ())
                    if candidate_ref in states
                ],
                "valid_candidates": valid_candidates,
                "decision": explicit,
                "resolved": resolved,
                "ambiguous": len(after_suggestions.get(ref, ())) > 1,
            }
        )
        relationship_touched.add(ref)

    if workspace["units"]:
        changed_units = set(parent_overrides)
        for ref, state in states.items():
            if any(row["unit_id"] in changed_units for row in state["leadership"]):
                management_touched.add(ref)

    return {
        "states": states,
        "errors": list(dict.fromkeys(errors)),
        "parent_overrides": parent_overrides,
        "relationship_reviews": relationship_reviews,
        "person_field_touched_refs": person_field_touched,
        "work_touched_refs": work_touched,
        "leadership_touched_refs": leadership_touched,
        "relationship_touched_refs": relationship_touched,
        "management_touched_refs": management_touched,
    }


def _stage_existing_person(workspace, values, bundle, user):
    person_id = _positive_int(values.get("person_id"), "employee")
    person = bundle.people_by_id.get(person_id)
    if not person or not person.active:
        raise ValueError("Select an active employee.")
    _require_person_scope(person, user, bundle)
    ref = f"p:{person.id}"
    changes = dict(workspace["people"].get(ref, {}).get("changes", {}))
    source = {
        "employee_id": person.employee_id,
        "first_name": person.first_name,
        "last_name": person.last_name,
        "seniority_date": person.seniority_date.isoformat(),
        "phone_number": person.phone_number,
        "classification": person.classification,
        "employee_status": person.employee_status,
        "active": bool(person.active),
        "work_area_unit_id": (
            bundle.work_by_person[person.id].work_area_unit_id
            if bundle.work_by_person.get(person.id)
            and bundle.work_by_person[person.id].active
            else None
        ),
    }
    selected = False
    for field in PERSON_FIELDS:
        if not _boolean(values.get(f"change_{field}")):
            continue
        selected = True
        normalized = _normalize_person_field(field, values.get(field), bundle)
        if field == "work_area_unit_id" and normalized is not None:
            actor = _actor_context(user, bundle)
            if (
                not actor["can_cross_area"]
                and normalized not in actor["allowed_unit_ids"]
            ):
                raise ValueError("The selected Work Area is outside your normal staffing scope.")
        if normalized == source[field]:
            changes.pop(field, None)
        else:
            changes[field] = normalized
    if not selected:
        raise ValueError("Select at least one employee field to stage.")
    if changes:
        workspace["people"][ref] = {
            "kind": "existing",
            "person_id": person.id,
            "changes": changes,
            "request_note": _optional_text(values.get("request_note")),
        }
    else:
        workspace["people"].pop(ref, None)


def _stage_new_person(workspace, values, bundle, user):
    actor = _actor_context(user, bundle)
    normalized = _normalize_new_person(values, bundle)
    if not actor["can_cross_area"]:
        target_area_id = normalized.get("work_area_unit_id")
        if not target_area_id or target_area_id not in actor["allowed_unit_ids"]:
            raise ValueError("New employees must be staged inside your normal staffing scope.")
    ref = f"n:{uuid.uuid4().hex}"
    workspace["people"][ref] = {
        "kind": "new",
        "temp_id": ref.removeprefix("n:"),
        "values": normalized,
        "request_note": _optional_text(values.get("request_note")),
    }


def _stage_leadership_add(workspace, values, bundle, user):
    ref = _person_ref(values.get("person_ref"))
    _require_ref_scope(ref, user, bundle, workspace)
    unit_id = _positive_int(values.get("unit_id"), "staffing unit")
    unit = bundle.units_by_id.get(unit_id)
    if not unit or not unit.active:
        raise ValueError("Select an active staffing unit.")
    actor = _actor_context(user, bundle)
    if not actor["can_cross_area"] and unit.id not in actor["allowed_unit_ids"]:
        raise ValueError("The selected unit is outside your normal staffing scope.")
    level = str(values.get("leadership_level") or unit.unit_type).strip()
    row = {"person_ref": ref, "unit_id": unit.id, "leadership_level": level}
    if row not in workspace["leadership_add"]:
        workspace["leadership_add"].append(row)


def _stage_leadership_remove(workspace, values, bundle, user):
    assignment_id = _positive_int(values.get("assignment_id"), "management assignment")
    assignment = bundle.leadership_by_id.get(assignment_id)
    if not assignment or not assignment.active:
        raise ValueError("Select an active management assignment.")
    _require_person_scope(bundle.people_by_id[assignment.person_id], user, bundle)
    if assignment_id not in workspace["leadership_remove"]:
        workspace["leadership_remove"].append(assignment_id)


def _stage_reporting(workspace, values, bundle, user):
    ref = _person_ref(values.get("person_ref"))
    _require_ref_scope(ref, user, bundle, workspace)
    action = str(values.get("relationship_action") or "").strip()
    if action not in {"keep", "change", "clear"}:
        raise ValueError("Choose Keep Current, Change, or Clear Reports To.")
    target_ref = None
    if action == "change":
        target_ref = _person_ref(values.get("reports_to_ref"))
    workspace["reporting"][ref] = {
        "action": action,
        "target_ref": target_ref,
    }


def _stage_unit(workspace, values, bundle, user):
    actor = _actor_context(user, bundle)
    if not actor["can_stage_structure"]:
        raise ValueError("Master access is required to stage structural unit changes.")
    unit_id = _positive_int(values.get("unit_id"), "staffing unit")
    parent_id = _positive_int(values.get("parent_id"), "parent unit")
    unit = bundle.units_by_id.get(unit_id)
    parent = bundle.units_by_id.get(parent_id)
    if not unit or not parent:
        raise ValueError("Select an existing staffing unit and parent.")
    workspace["units"][str(unit.id)] = {"parent_id": parent.id}


def _remove_change(workspace, values):
    kind = str(values.get("change_kind") or "").strip()
    key = str(values.get("change_key") or "").strip()
    if kind == "person":
        workspace["people"].pop(key, None)
    elif kind == "leadership_add":
        try:
            workspace["leadership_add"].pop(int(key))
        except (ValueError, IndexError):
            pass
    elif kind == "leadership_remove":
        try:
            workspace["leadership_remove"].remove(int(key))
        except (ValueError, TypeError):
            pass
    elif kind == "reporting":
        workspace["reporting"].pop(key, None)
    elif kind == "unit":
        workspace["units"].pop(key, None)
    else:
        raise ValueError("Choose a staged change to remove.")


def _staged_groups(workspace, simulation, bundle):
    groups = []
    baseline_states = _baseline_states(bundle)
    for ref, staged in workspace["people"].items():
        state = simulation["states"].get(ref)
        if not state:
            continue
        changes = staged.get("values") if staged.get("kind") == "new" else staged.get("changes")
        baseline = baseline_states.get(ref)
        groups.append(
            {
                "kind": "person",
                "key": ref,
                "title": f"{state['first_name']} {state['last_name']} - {state['employee_id']}",
                "subtitle": "New Person" if staged.get("kind") == "new" else "Employee Changes",
                "items": [
                    {
                        "label": _field_label(field),
                        "current": (
                            "New record"
                            if baseline is None
                            else _display_value(field, baseline.get(field), bundle)
                        ),
                        "value": _display_value(field, value, bundle),
                    }
                    for field, value in changes.items()
                ],
            }
        )
    for index, row in enumerate(workspace["leadership_add"]):
        state = simulation["states"].get(row["person_ref"])
        unit = bundle.units_by_id.get(row["unit_id"])
        groups.append(
            {
                "kind": "leadership_add",
                "key": str(index),
                "title": f"{state['first_name']} {state['last_name']}" if state else "Management Person",
                "subtitle": "Add Operational Assignment",
                "items": [{"label": "Unit", "current": "Unassigned", "value": bundle.unit_path(unit.id) if unit else "Missing"}],
            }
        )
    for assignment_id in workspace["leadership_remove"]:
        row = bundle.leadership_by_id.get(assignment_id)
        person = bundle.people_by_id.get(row.person_id) if row else None
        unit = bundle.units_by_id.get(row.unit_id) if row else None
        groups.append(
            {
                "kind": "leadership_remove",
                "key": str(assignment_id),
                "title": person.full_name if person else "Management Person",
                "subtitle": "Remove Operational Assignment",
                "items": [{"label": "Unit", "current": bundle.unit_path(unit.id) if unit else "Missing", "value": "Unassigned"}],
            }
        )
    for ref, decision in workspace["reporting"].items():
        state = simulation["states"].get(ref)
        target = simulation["states"].get(decision.get("target_ref"))
        value = "Keep Current" if decision["action"] == "keep" else "Clear"
        if decision["action"] == "change" and target:
            value = f"{target['first_name']} {target['last_name']}"
        groups.append(
            {
                "kind": "reporting",
                "key": ref,
                "title": f"{state['first_name']} {state['last_name']}" if state else "Management Person",
                "subtitle": "Reports To Decision",
                "items": [{"label": "Decision", "current": "Current relationship", "value": value}],
            }
        )
    for unit_id_text, row in workspace["units"].items():
        unit = bundle.units_by_id.get(int(unit_id_text))
        groups.append(
            {
                "kind": "unit",
                "key": unit_id_text,
                "title": unit.name if unit else "Staffing Unit",
                "subtitle": "Reparent Unit",
                "items": [{"label": "Parent", "current": bundle.unit_path(unit.parent_id) if unit and unit.parent_id else "None", "value": bundle.unit_path(row["parent_id"])}],
            }
        )
    return groups


def _submission_unsupported_items(workspace, simulation, bundle):
    unsupported = []
    for ref, staged in workspace["people"].items():
        state = simulation["states"].get(ref)
        title = (
            f"{state['first_name']} {state['last_name']}" if state else "Employee"
        )
        if staged.get("kind") == "new":
            unsupported.append(
                {"person_ref": ref, "field": "new_person", "label": f"{title}: new person creation"}
            )
            continue
        person = bundle.people_by_id.get(staged.get("person_id"))
        if not person or person.classification not in staffing_service.NON_MANAGEMENT_CLASSIFICATIONS:
            unsupported.append(
                {"person_ref": ref, "field": "management_person", "label": f"{title}: management employee changes"}
            )
            continue
        for field, value in staged.get("changes", {}).items():
            if field not in REQUEST_SUPPORTED_FIELDS:
                unsupported.append(
                    {"person_ref": ref, "field": field, "label": f"{title}: {_field_label(field)}"}
                )
            elif (
                field == "classification"
                and value not in staffing_service.WRITABLE_NON_MANAGEMENT_CLASSIFICATIONS
            ):
                unsupported.append(
                    {"person_ref": ref, "field": field, "label": f"{title}: management classification"}
                )
    if workspace["leadership_add"] or workspace["leadership_remove"]:
        unsupported.append({"person_ref": None, "field": "leadership", "label": "Management operational assignments"})
    if workspace["reporting"]:
        unsupported.append({"person_ref": None, "field": "reporting", "label": "Reports To changes"})
    if workspace["units"]:
        unsupported.append({"person_ref": None, "field": "structure", "label": "Structural unit changes"})
    return unsupported


def _validate_direct_authority(workspace, simulation, actor, user):
    person_changes = bool(workspace["people"])
    management_change = bool(
        workspace["leadership_add"]
        or workspace["leadership_remove"]
        or workspace["reporting"]
        or simulation["management_touched_refs"]
    )
    structural_change = bool(workspace["units"])
    if person_changes and not (
        actor["is_grandmaster"] or user_can(PEOPLE_EDIT_PERMISSION, user)
    ):
        raise ValueError("You do not have permission to apply employee record changes.")
    if management_change and not (
        actor["is_grandmaster"]
        or (
            actor["can_direct_management"]
            and user_can(MANAGEMENT_ASSIGN_PERMISSION, user)
        )
    ):
        raise ValueError(
            "Direct management changes require an eligible FT Supervisor, Manager, Division Manager, or Grandmaster."
        )
    if structural_change and not (
        actor["is_grandmaster"]
        or (
            actor["can_stage_structure"]
            and actor["can_direct_management"]
            and user_can(ORG_CHART_EDIT_STRUCTURE_PERMISSION, user)
        )
    ):
        raise ValueError("You do not have authority to apply structural unit changes.")


def _validate_workspace_scope(workspace, simulation, actor, bundle):
    if actor["can_cross_area"] or actor["is_grandmaster"]:
        return
    subject_refs = set(workspace["people"]) | set(workspace["reporting"])
    subject_refs.update(row["person_ref"] for row in workspace["leadership_add"])
    subject_refs.update(
        f"p:{bundle.leadership_by_id[assignment_id].person_id}"
        for assignment_id in workspace["leadership_remove"]
        if assignment_id in bundle.leadership_by_id
    )
    for ref in subject_refs:
        state = simulation["states"].get(ref)
        if not state:
            raise ValueError("A staged employee no longer exists.")
        if state["is_new"]:
            if state["work_area_unit_id"] not in actor["allowed_unit_ids"]:
                raise ValueError("A staged person is outside your normal staffing scope.")
            continue
        person = bundle.people_by_id.get(state["person_id"])
        assignment = bundle.work_by_person.get(state["person_id"])
        in_scope = bool(
            assignment
            and assignment.active
            and assignment.work_area_unit_id in actor["allowed_unit_ids"]
        ) or any(
            row.unit_id in actor["allowed_unit_ids"]
            for row in bundle.leadership_by_person.get(state["person_id"], [])
        )
        if not person or not in_scope:
            raise ValueError("A staged person is outside your normal staffing scope.")
        if ref in workspace["people"] and (
            state["work_area_unit_id"] is not None
            and state["work_area_unit_id"] not in actor["allowed_unit_ids"]
        ):
            raise ValueError("A staged Work Area is outside your normal staffing scope.")
    for row in workspace["leadership_add"]:
        if row["unit_id"] not in actor["allowed_unit_ids"]:
            raise ValueError("A staged management assignment is outside your normal staffing scope.")


def _actor_context(user, bundle):
    app_role = get_user_app_role(user, "neostaffing") or "watcher"
    person = bundle.person_for_user(user)
    is_grandmaster = bool(
        app_role == "grandmaster" or getattr(user, "role", None) == "grandmaster"
    )
    can_cross_area = is_grandmaster or ROLE_LEVELS.get(app_role, 0) >= ROLE_LEVELS["simulator"]
    can_stage_structure = is_grandmaster or ROLE_LEVELS.get(app_role, 0) >= ROLE_LEVELS["master"]
    owned_roots = {
        row.unit_id
        for row in bundle.leadership_by_person.get(getattr(person, "id", None), [])
    }
    allowed_unit_ids = set(bundle.units_by_id) if can_cross_area else _descendant_ids(owned_roots, bundle)
    return {
        "app_role": app_role,
        "person": person,
        "is_grandmaster": is_grandmaster,
        "is_pt_supervisor": bool(person and person.classification == "part_time_supervisor"),
        "can_cross_area": can_cross_area,
        "can_stage_structure": can_stage_structure,
        "can_direct_management": bool(
            is_grandmaster
            or (
                person
                and person.classification
                in staffing_service.DIRECT_REPORTING_EDITOR_CLASSIFICATIONS
            )
        ),
        "allowed_unit_ids": allowed_unit_ids,
    }


def _require_person_scope(person, user, bundle):
    actor = _actor_context(user, bundle)
    if actor["can_cross_area"] or actor["is_grandmaster"]:
        return
    assignment = bundle.work_by_person.get(person.id)
    if assignment and assignment.active and assignment.work_area_unit_id in actor["allowed_unit_ids"]:
        return
    if any(
        row.unit_id in actor["allowed_unit_ids"]
        for row in bundle.leadership_by_person.get(person.id, [])
    ):
        return
    raise ValueError("This person is outside your normal staffing scope.")


def _require_ref_scope(ref, user, bundle, workspace):
    if ref.startswith("p:"):
        person = bundle.people_by_id.get(int(ref.partition(":")[2]))
        if not person:
            raise ValueError("The selected person was not found.")
        _require_person_scope(person, user, bundle)
        return
    staged = workspace["people"].get(ref)
    if not staged or staged.get("kind") != "new":
        raise ValueError("The selected staged person was not found.")


def _require_bulk_access(user):
    if not user_can(BULK_CHANGE_PERMISSION, user):
        raise ValueError("You do not have permission to use Bulk Change.")


def _ensure_workspace_revision(workspace, bundle):
    if workspace["base_revision"] is None:
        workspace["base_revision"] = bundle.revision
    elif workspace["base_revision"] != bundle.revision:
        raise ValueError(LIVE_DATA_CHANGED_MESSAGE)


def _require_current_revision(workspace, bundle):
    if not _workspace_has_changes(workspace):
        raise ValueError("Stage at least one change before the final action.")
    if not workspace.get("base_revision") or workspace["base_revision"] != bundle.revision:
        raise ValueError(LIVE_DATA_CHANGED_MESSAGE)


def _baseline_states(bundle):
    empty = new_workspace(type("Owner", (), {"id": 0})())
    return _simulate_baseline(empty, bundle)


def _simulate_baseline(_workspace, bundle):
    states = {}
    for person in bundle.people:
        assignment = bundle.work_by_person.get(person.id)
        relationship = bundle.relationship_by_person.get(person.id)
        states[f"p:{person.id}"] = {
            "ref": f"p:{person.id}",
            "is_new": False,
            "person_id": person.id,
            "employee_id": person.employee_id,
            "first_name": person.first_name,
            "last_name": person.last_name,
            "seniority_date": person.seniority_date.isoformat(),
            "phone_number": person.phone_number,
            "classification": person.classification,
            "employee_status": person.employee_status,
            "active": bool(person.active),
            "work_area_unit_id": assignment.work_area_unit_id if assignment and assignment.active else None,
            "leadership": [
                {
                    "assignment_id": row.id,
                    "unit_id": row.unit_id,
                    "leadership_level": row.leadership_level,
                }
                for row in bundle.leadership_by_person.get(person.id, [])
            ],
            "reports_to_ref": f"p:{relationship.reports_to_person_id}" if relationship else None,
        }
    return states


def _all_suggestions(states, bundle, parent_overrides):
    assignments_by_unit = defaultdict(list)
    for state in states.values():
        if not state["active"]:
            continue
        for row in state["leadership"]:
            assignments_by_unit[row["unit_id"]].append(state)
    result = {}
    for ref, state in states.items():
        target_classification = staffing_service.REPORTING_TARGET_CLASSIFICATION.get(
            state["classification"]
        )
        if not state["active"] or not target_classification:
            continue
        fallback = {
            candidate["ref"]
            for candidate in states.values()
            if candidate["active"]
            and candidate["classification"] == target_classification
            and candidate["ref"] != ref
        }
        candidate_sets = []
        for assignment in state["leadership"]:
            owner_unit_ids = _owner_unit_ids(
                state["classification"],
                assignment["unit_id"],
                bundle,
                parent_overrides,
            )
            owner_refs = {
                owner["ref"]
                for unit_id in owner_unit_ids
                for owner in assignments_by_unit.get(unit_id, [])
                if owner["classification"] == target_classification
                and owner["ref"] != ref
            }
            candidate_sets.append(owner_refs or fallback)
        suggested = set().union(*candidate_sets) if candidate_sets else fallback
        result[ref] = tuple(sorted(suggested))
    return result


def _owner_unit_ids(classification, unit_id, bundle, parent_overrides):
    unit = bundle.units_by_id.get(unit_id)
    if not unit:
        return set()
    parent_id = parent_overrides.get(unit.id, unit.parent_id)
    parent = bundle.units_by_id.get(parent_id)
    if classification == "part_time_supervisor":
        return {parent.id} if parent and parent.unit_type == "department" else set()
    if classification == "full_time_supervisor":
        return {parent.id} if parent and parent.unit_type == "operation" else set()
    if classification == "manager":
        return {parent.id} if parent and parent.unit_type == "sort" else set()
    if classification == "full_time_specialist":
        if unit.unit_type == "department":
            return {unit.id}
        if unit.unit_type == "operation":
            return {
                child.id
                for child in bundle.units
                if child.unit_type == "department"
                and parent_overrides.get(child.id, child.parent_id) == unit.id
            }
    return set()


def _validate_person_states(states, bundle):
    errors = []
    employee_ids = defaultdict(list)
    for state in states.values():
        employee_ids[state["employee_id"].strip().lower()].append(state)
        if state["classification"] not in STAFFING_DATABASE_CLASSIFICATIONS:
            errors.append("A staged employee has an unsupported classification.")
        if state["employee_status"] not in STAFFING_EMPLOYEE_STATUSES:
            errors.append("A staged employee has an unsupported employee status.")
        if state["work_area_unit_id"] is not None:
            unit = bundle.units_by_id.get(state["work_area_unit_id"])
            if not unit or not unit.active or unit.unit_type != "work_area":
                errors.append("A staged Work Area is no longer available.")
        for assignment in state["leadership"]:
            if not _leadership_is_valid(
                state["classification"],
                bundle.units_by_id.get(assignment["unit_id"]),
                assignment["leadership_level"],
            ):
                errors.append(
                    f"{state['first_name']} {state['last_name']} has an invalid operational assignment."
                )
    if any(len(rows) > 1 for rows in employee_ids.values()):
        errors.append("Employee ID already exists in the staged final state.")
    return errors


def _validate_parent_overrides(bundle, parent_overrides):
    errors = []
    expected_parent = staffing_service.PARENT_TYPE_BY_UNIT_TYPE
    for unit_id, parent_id in parent_overrides.items():
        unit = bundle.units_by_id.get(unit_id)
        parent = bundle.units_by_id.get(parent_id)
        if not unit or not parent:
            errors.append("A staged staffing unit or parent no longer exists.")
            continue
        allowed = expected_parent.get(unit.unit_type)
        if allowed is None:
            errors.append("Sort units cannot be reparented.")
        elif isinstance(allowed, tuple) and parent.unit_type not in allowed:
            errors.append(f"{unit.name} cannot be placed under {parent.name}.")
        elif isinstance(allowed, str) and parent.unit_type != allowed:
            errors.append(f"{unit.name} cannot be placed under {parent.name}.")
        visited = {unit.id}
        current = parent
        while current:
            if current.id in visited:
                errors.append("A staffing unit cannot move under one of its descendants.")
                break
            visited.add(current.id)
            current = bundle.units_by_id.get(
                parent_overrides.get(current.id, current.parent_id)
            )
    return errors


def _target_is_valid(subject, target):
    required = staffing_service.REPORTING_TARGET_CLASSIFICATION.get(
        subject["classification"]
    )
    return bool(
        required
        and target
        and target["active"]
        and target["ref"] != subject["ref"]
        and target["classification"] == required
    )


def _baseline_reports_to_ref(ref, bundle):
    if not ref.startswith("p:"):
        return None
    relationship = bundle.relationship_by_person.get(int(ref.partition(":")[2]))
    return f"p:{relationship.reports_to_person_id}" if relationship else None


def _leadership_is_valid(classification, unit, level):
    return bool(
        unit
        and level == unit.unit_type
        and unit.unit_type in ASSIGNMENT_UNIT_TYPES.get(classification, set())
    )


def _normalize_new_person(values, bundle):
    work_area = _normalize_person_field(
        "work_area_unit_id", values.get("work_area_unit_id"), bundle
    )
    return {
        "employee_id": _required_text(values.get("employee_id"), "Employee ID"),
        "first_name": _required_text(values.get("first_name"), "First name"),
        "last_name": _required_text(values.get("last_name"), "Last name"),
        "seniority_date": _date_text(values.get("seniority_date")),
        "phone_number": _optional_text(values.get("phone_number")),
        "classification": _choice(values.get("classification"), STAFFING_CLASSIFICATIONS, "classification"),
        "employee_status": _choice(values.get("employee_status") or "active", STAFFING_EMPLOYEE_STATUSES, "employee status"),
        "active": _boolean(values.get("active", "1")),
        "work_area_unit_id": work_area,
    }


def _normalize_person_field(field, value, bundle):
    if field in {"employee_id", "first_name", "last_name"}:
        return _required_text(value, _field_label(field))
    if field == "seniority_date":
        return _date_text(value)
    if field == "phone_number":
        return _optional_text(value)
    if field == "classification":
        return _choice(value, STAFFING_CLASSIFICATIONS, "classification")
    if field == "employee_status":
        return _choice(value, STAFFING_EMPLOYEE_STATUSES, "employee status")
    if field == "active":
        return _boolean(value)
    if field == "work_area_unit_id":
        if value in (None, "", "__clear__", "__unassigned__"):
            return None
        unit_id = _positive_int(value, "Work Area")
        unit = bundle.units_by_id.get(unit_id)
        if not unit or not unit.active or unit.unit_type != "work_area":
            raise ValueError("Select an active Work Area.")
        return unit.id
    raise ValueError("Unsupported employee field.")


def _bundle_revision(bundle):
    payload = {
        "units": [
            [row.id, row.parent_id, row.unit_type, row.name, bool(row.active), _timestamp(row.updated_at)]
            for row in bundle.units
        ],
        "people": [
            [
                row.id,
                row.employee_id,
                row.first_name,
                row.last_name,
                row.seniority_date.isoformat(),
                row.phone_number,
                row.classification,
                row.employee_status,
                bool(row.active),
                _timestamp(row.updated_at),
            ]
            for row in bundle.people
        ],
        "work": [
            [row.id, row.person_id, row.work_area_unit_id, bool(row.active), _timestamp(row.updated_at)]
            for row in bundle.work_assignments
        ],
        "leadership": [
            [row.id, row.person_id, row.unit_id, row.leadership_level, bool(row.active), _timestamp(row.updated_at)]
            for row in bundle.leadership_assignments
        ],
        "reporting": [
            [row.id, row.person_id, row.reports_to_person_id, _timestamp(row.updated_at)]
            for row in bundle.reporting_relationships
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _workspace_has_changes(workspace):
    return any(
        (
            workspace["people"],
            workspace["leadership_add"],
            workspace["leadership_remove"],
            workspace["reporting"],
            workspace["units"],
        )
    )


def _descendant_ids(root_ids, bundle):
    result = set()
    stack = list(root_ids)
    while stack:
        unit_id = stack.pop()
        if unit_id in result:
            continue
        result.add(unit_id)
        stack.extend(child.id for child in bundle.children_by_parent.get(unit_id, []))
    return result


def _field_label(field):
    return {
        "employee_id": "Employee ID",
        "first_name": "First Name",
        "last_name": "Last Name",
        "seniority_date": "Seniority Date",
        "phone_number": "Phone",
        "classification": "Classification",
        "employee_status": "Employee Status",
        "active": "Active",
        "work_area_unit_id": "Work Area",
    }.get(field, field.replace("_", " ").title())


def _display_value(field, value, bundle):
    if field == "work_area_unit_id":
        return bundle.unit_path(value) if value else "Unassigned"
    if field == "classification":
        return staffing_service.CLASSIFICATION_LABELS.get(value, value)
    if field == "employee_status":
        return staffing_service.EMPLOYEE_STATUS_LABELS.get(value, value)
    if field == "active":
        return "Active" if value else "Inactive"
    return str(value if value is not None else "Blank")


def _serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt=WORKSPACE_SALT)


def _load_rows(query, lock):
    return (query.with_for_update() if lock else query).all()


def _person_ref(value):
    text = str(value or "").strip()
    if text.startswith("p:"):
        _positive_int(text.partition(":")[2], "person")
        return text
    if text.startswith("n:") and text.partition(":")[2]:
        return text
    raise ValueError("Select a valid person.")


def _positive_int(value, label):
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Select a valid {label}.")
    if normalized <= 0:
        raise ValueError(f"Select a valid {label}.")
    return normalized


def _required_text(value, label):
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required.")
    return text


def _optional_text(value):
    text = str(value or "").strip()
    return text or None


def _choice(value, choices, label):
    normalized = str(value or "").strip().lower()
    if normalized not in choices:
        raise ValueError(f"Choose a valid {label}.")
    return normalized


def _date_text(value):
    try:
        return date.fromisoformat(str(value or "").strip()).isoformat()
    except ValueError as error:
        raise ValueError("Seniority Date must be a valid date.") from error


def _boolean(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _timestamp(value):
    return value.isoformat(timespec="microseconds") if value else ""


def _person_sort_key(person):
    return (
        person.last_name.lower(),
        person.first_name.lower(),
        person.employee_id.lower(),
        person.id,
    )
