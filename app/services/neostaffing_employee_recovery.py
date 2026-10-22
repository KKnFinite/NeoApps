"""Atomic employee deletion and conflict-safe, encrypted 24-hour recovery.

Never follow outgoing FKs (especially User, aircraft, sort and organizational
parents). Shared operational rows are detached, not deleted. New/unclassified
dependent tables fail closed before any deletion, rather than losing data.
"""
import base64
import hashlib
import json
import zlib
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from cryptography.fernet import Fernet, MultiFernet, InvalidToken
from flask import current_app
from sqlalchemy import Date, DateTime, Numeric, LargeBinary, and_, or_, select, text

from app.extensions import db
from app.models import StaffingPerson
from app.models.staffing_employee_recovery import StaffingEmployeeRecovery as Recovery, StaffingEmployeeRecoveryAudit as Audit
from app.services.access_control import user_has_app_access, user_can_access_app
from app.services.permission_rules import user_can
from app.services.live_collaboration import entity_version
from app.services.neostaffing_write_authority import lock_staffing_write_authority

PERMISSION = 'neostaffing.people.edit'
RETENTION = timedelta(hours=24)
OWNED = frozenset('''staffing_people staffing_leadership_assignments staffing_change_requests
staffing_change_request_items staffing_change_request_events staffing_notifications
staffing_daily_attendance staffing_timecard_slices staffing_timecard_segments staffing_timecard_edits
staffing_attendance_occurrences staffing_employee_records staffing_employee_record_events
staffing_combo_workdays staffing_accountability_workdays staffing_accountability_sources
staffing_accountability_resolutions staffing_accountability_coverage staffing_accountability_reconciliations
staffing_person_qualifications staffing_shift_flow_plans staffing_reporting_relationships
staffing_twenty_c_affiliations staffing_work_assignments staffing_vacation_union_selections
staffing_vacation_week_conversions staffing_vacation_day_selections staffing_vacation_day_entitlements
staffing_vacation_management_selections staffing_vacation_management_turn_resolutions
staffing_vacation_management_change_requests neorain_load_planner_contacts
neorain_crew_admin_assignments neosubzero_callout_assignments'''.split())
SHARED = frozenset('''master_flight_schedules sort_date_missions neosubzero_ucc_assignments
neosubzero_spray_records staffing_vacation_management_turn_states'''.split())


class RecoveryConflict(ValueError):
    pass


def now_utc():
    # Deliberately independent of operational-sort/day overrides.
    return datetime.utcnow()


def authorize(actor, *, restore=False):
    actor = lock_staffing_write_authority(actor, [PERMISSION])
    if not user_has_app_access(actor,'neostaffing') or not user_can(PERMISSION, user=actor):
        raise ValueError('You do not have permission to delete employees.')
    if restore and not user_can_access_app(actor,'neostaffing',minimum_role='master'):
        raise ValueError('Employee recovery requires NeoStaffing administrator access.')
    return actor


def cipher():
    keys = [current_app.config.get('SECRET_KEY'), *(current_app.config.get('SECRET_KEY_FALLBACKS') or [])]
    if not keys[0]:
        raise ValueError('Secure recovery storage is unavailable. Employee was not deleted.')
    return MultiFernet([Fernet(base64.urlsafe_b64encode(hashlib.sha256(
        b'neoapps.employee-recovery.v1\0'+str(key).encode()).digest())) for key in keys if key])


def seal(payload):
    return cipher().encrypt(zlib.compress(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()))


def open_payload(recovery):
    try:
        return json.loads(zlib.decompress(cipher().decrypt(bytes(recovery.encrypted_payload))))
    except (InvalidToken, ValueError, TypeError, zlib.error):
        raise ValueError('Recovery backup cannot be verified. No employee data was changed.') from None


def encode(row):
    return {key:(value.isoformat() if isinstance(value,(date,datetime)) else str(value) if isinstance(value,Decimal)
                 else {'bytes':base64.b64encode(value).decode()} if isinstance(value,bytes) else value)
            for key,value in row.items()}


def decode(table, row):
    result = dict(row)
    for column in table.columns:
        value = result.get(column.name)
        if value is None: continue
        if isinstance(column.type,DateTime): result[column.name]=datetime.fromisoformat(value)
        elif isinstance(column.type,Date): result[column.name]=date.fromisoformat(value)
        elif isinstance(column.type,Numeric): result[column.name]=Decimal(value)
        elif isinstance(column.type,LargeBinary): result[column.name]=base64.b64decode(value['bytes'])
    return result


def identity(table, row):
    return tuple(row[c.name] for c in table.primary_key)


def predicate(table, row):
    return and_(*(c == row[c.name] for c in table.primary_key))


def identity_lock(person_id, *, wait=True):
    # Serializes file lifecycle across *successive* delete/restore cycles for
    # one person, not merely two actions against the same recovery row.
    if db.session.get_bind().dialect.name=='postgresql':
        function='pg_advisory_xact_lock' if wait else 'pg_try_advisory_xact_lock'
        result=db.session.scalar(text(f'SELECT {function}(846102,:person)'),{'person':person_id})
        return True if wait else result
    return True


def sqlite_backup_lock(recovery_id):
    if db.session.get_bind().dialect.name=='sqlite':
        # SQLite has no FOR UPDATE; reserve its writer before touching files.
        db.session.execute(Recovery.__table__.update().where(Recovery.id==recovery_id).values(state=Recovery.state))


def _other_owner(table, row, person_id):
    owners = [fk.parent.name for fk in table.foreign_keys
              if fk.column.table.name=='staffing_people' and not fk.parent.nullable]
    return bool(owners and all(row[key] != person_id for key in owners))


def collect(person):
    """Lock a complete incoming-FK closure, stopping at shared operational rows."""
    tables = db.metadata.tables
    root = StaffingPerson.__table__
    original = dict(db.session.execute(select(root).where(root.c.id==person.id)).mappings().one())
    deleted = {root.name:{identity(root,original):original}}
    patches = {}
    changed = True
    while changed:
        changed = False
        for table in sorted(tables.values(),key=lambda t:t.name):
            refs = [fk for fk in table.foreign_keys if fk.column.table.name in deleted]
            for fk in sorted(refs,key=lambda f:f.parent.name):
                targets = {row[fk.column.name] for row in deleted[fk.column.table.name].values()}
                rows = db.session.execute(select(table).where(fk.parent.in_(targets)).order_by(
                    *table.primary_key.columns).with_for_update()).mappings().all()
                for source in rows:
                    row = dict(source); key = identity(table,row)
                    if key in deleted.get(table.name,{}): continue
                    # Nullable actor references on someone else's request, or
                    # operational flight/spray/position rows, are not ownership.
                    detach = (table.name in SHARED or _other_owner(table,row,person.id))
                    if detach:
                        if not fk.parent.nullable:
                            raise ValueError('Conflicting employee ownership requires review before deletion: '+table.name)
                        patch = patches.setdefault((table.name,key),dict(table=table.name,before=encode(row),fields={}))
                        patch['fields'][fk.parent.name] = None
                    else:
                        if table.name not in OWNED:
                            raise ValueError('Employee dependency needs a safe deletion policy: '+table.name)
                        deleted.setdefault(table.name,{})[key] = row
                        patches.pop((table.name,key),None)
                        changed = True
    # Routing lists are references, not ownership. Keep every other approver.
    table = tables['staffing_change_requests']
    for source in db.session.execute(select(table).order_by(table.c.id).with_for_update()).mappings():
        row=dict(source); key=identity(table,row)
        if key in deleted.get(table.name,{}): continue
        routed=json.loads(row['routed_approver_person_ids_json'] or '[]')
        filtered=[identifier for identifier in routed if str(identifier)!=str(person.id)]
        if filtered != routed:
            patch=patches.setdefault((table.name,key),dict(table=table.name,before=encode(row),fields={}))
            patch['fields']['routed_approver_person_ids_json']=json.dumps(filtered)
    return deleted,list(patches.values())


def ordered_tables(deleted):
    remaining=set(deleted); result=[]
    while remaining:
        ready=sorted(name for name in remaining if not {
            fk.column.table.name for fk in db.metadata.tables[name].foreign_keys
            if fk.column.table.name != name} & remaining)
        if not ready: raise ValueError('Employee dependency cycle requires review; nothing was deleted.')
        result.extend(ready); remaining.difference_update(ready)
    return result


def check_restore_parents(payload):
    """Do not reconstruct deleted organizational/User/operational parents."""
    planned={name:[decode(db.metadata.tables[name],row) for row in rows] for name,rows in payload['rows'].items()}
    needed={}
    for name,rows in planned.items():
        for fk in db.metadata.tables[name].foreign_keys:
            target=(fk.column.table.name,fk.column.name)
            inserted={row[fk.column.name] for row in planned.get(target[0],[])}
            needed.setdefault(target,set()).update(row[fk.parent.name] for row in rows
                if row[fk.parent.name] is not None and row[fk.parent.name] not in inserted)
    for (name,column),values in sorted(needed.items()):
        if not values: continue
        target=db.metadata.tables[name].c[column]
        existing=set(db.session.scalars(select(target).where(target.in_(values)).with_for_update(read=True,key_share=True)))
        if existing != values:
            raise RecoveryConflict('Related records no longer exist: '+name+'. Resolve this dependency before restoring; nothing was overwritten.')


def files_for(deleted):
    from app.services import neostaffing_record_storage as storage
    result=[]
    for row in deleted.get('staffing_employee_records',{}).values():
        if not row.get('signature_key'): continue
        if not row['signature_key'].startswith('employee-records/'+row['id']+'/') or '..' in row['signature_key'].split('/'):
            raise ValueError('Signature identity requires review before employee deletion.')
        adapter=storage.backend()
        if not callable(getattr(adapter,'delete',None)):
            raise ValueError('Private signature storage must support automatic deletion before this employee can be deleted.')
        raw=storage.read(type('Record',(),row)())
        result.append(dict(key=row['signature_key'],sha256=row['signature_sha256'],data=base64.b64encode(raw).decode()))
    return result


def invalidate_weeks(payload):
    from app.services.neostaffing_timecards import lock_week, invalidate
    for day in sorted({date.fromisoformat(row['workday_date']) for row in payload['rows'].get('staffing_timecard_slices',[])}):
        invalidate(lock_week(day))


def delete_employee(actor, person_id, values):
    actor=authorize(actor)
    identity_lock(person_id)
    person=StaffingPerson.query.filter_by(id=person_id).populate_existing().with_for_update().first()
    if not person: raise RecoveryConflict('Employee no longer exists. Refresh the roster; do not repeat deletion.')
    if values.get('confirm_delete') != 'yes' or values.get('confirmed_employee_id') != person.employee_id:
        raise ValueError('Explicit employee deletion confirmation is required.')
    if not values.get('expected_person_version') or values['expected_person_version'] != entity_version(person):
        raise RecoveryConflict('Employee changed while you were editing. Close and reopen the editor before deleting.')
    from app.services import neostaffing as staffing
    home=staffing.assignment_service.shift_home(person)
    revision=staffing.shift_flow_revision(person,person.shift_flow_plan,home) if home else ''
    if values.get('expected_version','') != revision:
        raise RecoveryConflict('Employee assignments changed. Close and reopen the editor before deleting.')
    deleted,patches=collect(person)
    order=ordered_tables(deleted)
    payload=dict(format=1,person_id=person.id,order=order,
        rows={name:[encode(row) for row in rows.values()] for name,rows in deleted.items()},
        patches=patches,files=files_for(deleted))
    at=now_utc()
    backup=Recovery(id=str(uuid4()),person_id=person.id,actor_id=actor.id,created_at=at,
        expires_at=at+RETENTION,state='preparing',encrypted_payload=seal(payload))
    if db.session.get_bind().dialect.name=='postgresql':
        backup.transaction_id=db.session.scalar(text('SELECT txid_current()'))
    db.session.add(backup);db.session.flush()
    persisted=db.session.execute(select(Recovery.encrypted_payload).where(Recovery.id==backup.id)).scalar_one()
    if open_payload(type('Backup',(),{'encrypted_payload':persisted})()) != payload:
        raise ValueError('Recovery backup verification failed. Employee was not deleted.')
    if backup.transaction_id is not None:
        db.session.execute(text("SELECT set_config('neoapps.employee_deletion', :id, true)"),{'id':backup.id})
    invalidate_weeks(payload)
    for patch in patches:
        table=db.metadata.tables[patch['table']]
        before=decode(table,patch['before'])
        db.session.execute(table.update().where(predicate(table,before)).values(**patch['fields']))
        after=db.session.execute(select(table).where(predicate(table,before))).mappings().one()
        patch['after']=encode(after)
    # Save exact post-detachment guards for conflict-safe recovery as well.
    backup.encrypted_payload=seal(payload);db.session.flush()
    for name in reversed(order):
        table=db.metadata.tables[name]
        for row in deleted[name].values():
            if db.session.execute(table.delete().where(predicate(table,row))).rowcount != 1:
                raise RecoveryConflict('Employee data changed during deletion. Nothing was deleted; reopen the editor.')
    backup.state='deleted'
    db.session.add(Audit(recovery_id=backup.id,action='deleted',actor_id=actor.id,occurred_at=at,
        row_counts={name:len(rows) for name,rows in deleted.items()}))
    db.session.flush();db.session.expire_all()
    return backup


def restore_employee(actor, recovery_id):
    actor=authorize(actor,restore=True)
    person_id=db.session.scalar(select(Recovery.person_id).where(Recovery.id==recovery_id))
    if person_id is None: raise ValueError('Recovery backup not found.')
    identity_lock(person_id)
    sqlite_backup_lock(recovery_id)
    backup=Recovery.query.filter_by(id=recovery_id).populate_existing().with_for_update().first()
    if not backup: raise ValueError('Recovery backup not found.')
    at=now_utc()  # Recheck AFTER lock waits; never accept an expired form.
    if at >= backup.expires_at or backup.state=='purged':
        raise ValueError('The 24-hour recovery period has expired. This employee cannot be restored.')
    if backup.state=='restored':
        if db.session.get(StaffingPerson,backup.person_id): return backup.person_id
        raise RecoveryConflict('This backup was already restored. Use the most recent deletion instead.')
    if backup.state!='deleted': raise RecoveryConflict('Employee recovery is not available in this state.')
    payload=open_payload(backup)
    if payload.get('format')!=1 or payload.get('person_id')!=backup.person_id:
        raise ValueError('Recovery backup identity is invalid.')
    # All checks precede insertion. Unique/FK constraints remain authoritative
    # against concurrent creates, deleted parents and newer assignments.
    for name in payload['order']:
        if name not in OWNED: raise ValueError('Unsupported recovery data.')
        table=db.metadata.tables[name]
        for encoded in payload['rows'][name]:
            row=decode(table,encoded)
            if db.session.execute(select(table).where(predicate(table,row)).with_for_update()).first():
                raise RecoveryConflict('Existing records conflict with recovery: '+name+'. No records were overwritten.')
    for patch in payload['patches']:
        table=db.metadata.tables[patch['table']]; before=decode(table,patch['before'])
        current=db.session.execute(select(table).where(predicate(table,before)).with_for_update()).mappings().first()
        if not current or encode(current)!=patch['after']:
            raise RecoveryConflict('A related assignment changed after deletion: '+table.name+'. Resolve it before restoring.')
    check_restore_parents(payload)
    invalidate_weeks(payload)
    for name in payload['order']:
        table=db.metadata.tables[name]
        for row in payload['rows'][name]: db.session.execute(table.insert().values(**decode(table,row)))
    for patch in payload['patches']:
        table=db.metadata.tables[patch['table']]; before=decode(table,patch['before'])
        db.session.execute(table.update().where(predicate(table,before)).values(
            **{name:before[name] for name in patch['fields']}))
    if payload['files']:
        from app.services.neostaffing_record_storage import backend,MAX_BYTES
        adapter=backend()
        for item in payload['files']:
            raw=base64.b64decode(item['data'])
            try: existing=adapter.get(item['key'],max_bytes=MAX_BYTES)
            except (KeyError,FileNotFoundError):
                adapter.put(item['key'],raw,content_type='image/png',checksum=item['sha256'])
                existing=adapter.get(item['key'],max_bytes=MAX_BYTES)
            if hashlib.sha256(existing).hexdigest()!=item['sha256']:
                raise ValueError('Stored signature conflict. Recovery was rolled back.')
    # Invalidate old editor versions; preserve original data/visit timestamps.
    db.session.execute(StaffingPerson.__table__.update().where(StaffingPerson.id==backup.person_id).values(
        updated_at=at,shift_flow_version=StaffingPerson.shift_flow_version+1))
    backup.state='restored';backup.restored_at=at;backup.restored_by=actor.id
    db.session.add(Audit(recovery_id=backup.id,action='restored',actor_id=actor.id,occurred_at=at,
        row_counts={name:len(rows) for name,rows in payload['rows'].items()}))
    db.session.flush();db.session.expire_all()
    return backup.person_id


def recovery_list(actor, *, page=1):
    authorize(actor,restore=True)
    rows=Recovery.query.filter(Recovery.state=='deleted',Recovery.expires_at>now_utc()).order_by(
        Recovery.created_at.desc(),Recovery.id).offset((page-1)*100).limit(101).all()
    result=[]
    for backup in rows[:100]:
        person=open_payload(backup)['rows']['staffing_people'][0]
        result.append(dict(id=backup.id,name=person['first_name']+' '+person['last_name'],
            employee_id=person['employee_id'],created_at=backup.created_at,expires_at=backup.expires_at))
    return result,page,len(rows)>100


def purge_expired(*, at=None, limit=25, isolated=False):
    """Background-only; payload/file purging never depends on a page visit."""
    from app.models.staffing_employee_record import StaffingEmployeeRecord as Record
    at=at or now_utc()
    candidates=db.session.execute(select(Recovery.id,Recovery.person_id).where(
        Recovery.state.in_(('deleted','restored')),Recovery.expires_at<=at).order_by(
        Recovery.expires_at,Recovery.id).limit(limit)).all()
    count=0
    def purge(backup):
        payload=open_payload(backup)
        for item in payload['files']:
            # Restored or shared live signatures are not backup debris.
            if db.session.query(Record.id).filter_by(signature_key=item['key']).first(): continue
            from app.services.neostaffing_record_storage import backend
            try: backend().delete(item['key'])
            except (KeyError,FileNotFoundError): pass
        backup.encrypted_payload=None;backup.state='purged';backup.purged_at=at
        db.session.add(Audit(recovery_id=backup.id,action='purged',occurred_at=at,actor_id=None,row_counts={}))
        db.session.flush()
    for recovery_id,person_id in candidates:
        if not identity_lock(person_id,wait=False): continue
        sqlite_backup_lock(recovery_id)
        backup=Recovery.query.filter(Recovery.id==recovery_id,Recovery.state.in_(('deleted','restored')),
            Recovery.expires_at<=at).populate_existing().with_for_update(skip_locked=True).first()
        if backup is None: continue
        if not isolated:
            purge(backup);count+=1
            continue
        try:
            with db.session.begin_nested():purge(backup)
            count+=1
        except Exception as error:
            # One unavailable file must not trap other expired employees' data.
            current_app.logger.error('Employee recovery purge item failed: %s; retrying',type(error).__name__)
    db.session.flush()
    return count
