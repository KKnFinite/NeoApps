"""Isolated Gunicorn load fixture. Never import from application code.

Only local SQLite, synthetic accounts, offline verified HRRR replay and a
temporary private archive. No production credentials, providers or mutations.
"""
import json
import os
import threading
import time
import tempfile
from datetime import datetime, timedelta, time as clock_time
from pathlib import Path
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import User, PortalAppAccess, Gateway, SortDateOperation, SortDateMission, SortDateParkingAssignment, StaffingPerson, GatewaySortMatrix, SortTimelineSettings, SortTimelineSortSetting
from app.services.access_control import ensure_default_gateway_and_nodes, backfill_default_gateway_node_roles
from app.services.permission_rules import ensure_default_permission_rules
from app.services.password_policy import set_user_password

ROOT = Path(__file__).resolve().parents[1]
folder = Path(os.environ['FROST_LOAD_DIRECTORY']).resolve()
assert folder.is_relative_to(ROOT / 'instance') and 'phase17' in folder.name
folder.mkdir(parents=True, exist_ok=True)
database_directory = tempfile.TemporaryDirectory(prefix='neoapps-phase17-db-')
app = create_app(type('Config', (), dict(
    TESTING=True, SECRET_KEY='isolated-phase17-only', SESSION_COOKIE_SECURE=False,
    SQLALCHEMY_DATABASE_URI='sqlite:///' + str(Path(database_directory.name) / 'fixture.db'),
    SQLALCHEMY_ENGINE_OPTIONS={'pool_size': 3, 'max_overflow': 1, 'pool_timeout': 5},
    SQLALCHEMY_TRACK_MODIFICATIONS=False, CSRF_PROTECT_TESTING=True,
    GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=False, GOOGLE_MOTHERBRAIN_READER_ENABLED=False,
    GOOGLE_MOTHERBRAIN_SERVICE_ACCOUNT_JSON=None, GOOGLE_SERVICE_ACCOUNT_JSON=None,
    FLIGHT_API_ENABLED=False, LIVE_SCREEN_REFRESH_INTERVAL_MS=0,
    NEOAPPS_MEMORY_DIAGNOSTICS_ENABLED=False, FROST_HISTORY_EXECUTION='local')))
if os.environ.get('FROST_LOAD_PRIORITY') != '1':
    app.extensions.pop('frost_request_activity', None)
with app.app_context():
    db.create_all()
    ensure_default_gateway_and_nodes()
    ensure_default_permission_rules()
    # Current canonical scope plus realistic independent tail/parking rows.
    from app.services.operation_lifecycle import ensure_operational_sort_operations
    gateway=Gateway.query.filter_by(code='RFD').one()
    settings=SortTimelineSettings(gateway_id=gateway.id,gateway_code='RFD')
    db.session.add(settings);db.session.flush()
    db.session.add(SortTimelineSortSetting(settings_id=settings.id,gateway_id=gateway.id,gateway_code='RFD',
        sort_name='night',sort_window_start_local=clock_time(0),sort_window_end_local=clock_time(23,59)))
    for day in ('monday','tuesday','wednesday','thursday','friday','saturday','sunday'):
        db.session.add(GatewaySortMatrix(gateway_id=gateway.id,gateway_code='RFD',day_of_week=day,sort_name='night',is_active=True))
    db.session.commit()
    scope=ensure_operational_sort_operations(gateway)
    operation=(scope['created']+scope['existing'])[0]
    for i in range(40):
        tail=f'NTEST{i:03d}';at=datetime.utcnow()+timedelta(minutes=i*3)
        for kind in ('arrival','departure'):
            db.session.add(SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,
                sort_name=operation.sort_name,gateway_code='RFD',mission_type=kind,mission_source='manual',
                flight_number=f'5X{i+1000}',origin='SDF' if kind=='arrival' else 'RFD',
                destination='RFD' if kind=='arrival' else 'SDF',assigned_tail_number=tail,
                planned_datetime_utc=at,planned_datetime_local=at-timedelta(hours=5),
                departure_status='scheduled',arrival_status='scheduled',fuel_status='waiting'))
        ramp='ABCDE'[i//8]
        db.session.add(SortDateParkingAssignment(sort_date_operation_id=operation.id,tail_number=tail,ramp_code=ramp,position_code=f'{ramp}{i%8+1:02d}'))
    for i in range(80):
        db.session.add(StaffingPerson(employee_id=f'FIXTURE{i}',first_name='Local',last_name=f'Worker {i}',classification='part_time',seniority_date=datetime.now().date()))
    for i in range(20):
        if User.query.filter_by(username=f'load-{i}').first(): continue
        user = User(username=f'load-{i}', email=f'load-{i}@example.test', role='grandmaster',
                    email_verified_at=datetime.now(), first_name='Isolated', last_name=f'Operator {i}')
        set_user_password(user, 'LoadFixture123!')
        db.session.add(user); db.session.flush()
        backfill_default_gateway_node_roles(user, role='grandmaster')
        db.session.add(PortalAppAccess(user_id=user.id, app_code='neostaffing',
                                      status='approved', role='grandmaster', is_active=True))
    db.session.commit()
    (folder/'fixture-missions.json').write_text(json.dumps([
        dict(id=m.id,board='inbound' if m.mission_type=='arrival' else 'outbound')
        for m in SortDateMission.query.order_by(SortDateMission.id).limit(20)]))
    db.session.remove(); db.engine.dispose()

# In this systemd test the process sees the host cgroup namespace. Render sees
# its own container root. Read the actual isolated service PSI for parity.
from app.services import frost_resources as priority
def isolated_cpu_pressure():
    path=Path('/sys/fs/cgroup/system.slice/neoapps-phase17-baseline.service/cpu.pressure')
    try:return float(dict(v.split('=') for v in path.read_text().splitlines()[0].split()[1:])['avg10'])
    except (OSError,ValueError,KeyError,IndexError):return None
priority.cpu_pressure=isolated_cpu_pressure

# Strictly no remote calls in the fixture. HRRR source bytes below are local.
patch('requests.sessions.Session.request', side_effect=RuntimeError('Isolated provider unavailable')).start()
patch('urllib.request.urlopen', side_effect=RuntimeError('Isolated provider unavailable')).start()


def replay():
    from app.services import frost_hrrr_point as light, frost_vault as vault, frost_model as model
    cache = ROOT / 'instance/frost-local/phase14/parity/sources'
    proof = json.loads((cache.parent / 'parity-2026102821.json').read_text())
    class Cached:
        def __init__(self): self.receipts = []
        @staticmethod
        def guard():
            from app.services.frost_resources import checkpoint
            checkpoint()
            return True
        def get(self, url):
            self.guard()
            receipt = json.loads((cache / (vault.digest(('GET ' + url).encode()) + '.json')).read_text())
            raw = (cache / (receipt['sha256'] + '.source')).read_bytes()
            assert vault.digest(raw) == receipt['sha256']
            self.receipts.append(receipt)
            time.sleep(.01)  # Bounded simulated provider latency, excluded from skill claims.
            return raw, receipt
    point = light.read_point(Cached(), datetime.fromisoformat(proof['cycle_at_utc']), [c['hour'] for c in proof['checks']])
    for check in proof['checks']: assert light.normalized(point, check['hour']) == check['values']
    baseline = json.loads((ROOT / 'instance/frost-local/phase12/frozen/baseline.json').read_text())
    for entry in baseline['models']:
        Cached.guard()
        raw = (ROOT / 'instance/frost-local/phase12/frozen/artifacts' / (entry['sha256'] + '.json')).read_bytes()
        assert vault.digest(raw) == entry['sha256']
        artifact = json.loads(raw)
        for check in artifact['parity_checks']:
            assert abs(model.score_vector(artifact, check['features'], len(artifact['contract']['features'])) - check['score']) < 1e-9
    # Private, bounded local durability stand-in; no claims about R2 latency.
    raw = vault.canonical(point)
    path = folder / 'offline-replay.json'
    path.write_bytes(raw)
    assert vault.digest(path.read_bytes()) == vault.digest(raw)


def background():
    while True:
        try:
            if (folder / 'active').exists():
                started = time.monotonic()
                with app.app_context(): replay()
                with (folder / 'cycles.jsonl').open('a') as stream:
                    stream.write(json.dumps({'seconds': time.monotonic() - started, 'ok': True}) + '\n')
        except Exception as error:
            with (folder / 'cycles.jsonl').open('a') as stream:
                stream.write(json.dumps({'error': str(error)}) + '\n')
        time.sleep(3)


threading.Thread(target=background, name='isolated-frost-replay', daemon=True).start()
