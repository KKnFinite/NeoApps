"""Loopback-only subprocess fixture, not registered in the application routes."""
import json
import os
import runpy
import threading
from unittest.mock import patch
from app import create_app
from app.extensions import db
from app.services import neostaffing_employee_recovery as recovery

database=os.environ['NEOAPPS_LIFECYCLE_SQLITE']
# Use separate worker files when testing non-preloaded startup to avoid DDL races.
if os.environ['NEOAPPS_LIFECYCLE_PRELOAD']=='0':database+='.'+str(os.getpid())
app=create_app(type('Config',(),dict(TESTING=True,SECRET_KEY='isolated-lifecycle-fixture',
    SQLALCHEMY_DATABASE_URI='sqlite:///'+database,SQLALCHEMY_ENGINE_OPTIONS={},
    SQLALCHEMY_TRACK_MODIFICATIONS=False,NEOAPPS_MEMORY_DIAGNOSTICS_ENABLED=False)))
with app.app_context():db.create_all();db.session.remove();db.engine.dispose()
app.testing=False
checks=[]
def expiry_check(**kwargs):
    assert kwargs=={'isolated':True}
    checks.append(os.getpid())
    return 0
recovery.purge_expired=expiry_check
with patch('app.create_app',return_value=app):runpy.run_path('run.py')
import_started_expiry='employee_recovery_worker' in app.extensions

def application(environ,start_response):
    if environ.get('PATH_INFO')!='/runtime':return app(environ,start_response)
    from app.services.neostaffing_employee_recovery_worker import notify_recovery_worker
    import gc,time
    if os.environ.get('NEOAPPS_LIFECYCLE_MEMORY')=='1':
        from app.services.neoscorpion_learning_vault import _r2_client
        client=_r2_client(dict(endpoint='https://example.invalid',region='us-east-2',access_key='fixture',secret_key='fixture'))
        try:client.meta.service_model.operation_model('GetObject')
        finally:client.close();del client
        gc.collect()
    notify_recovery_worker(app)
    deadline=time.monotonic()+2
    while not checks and time.monotonic()<deadline:time.sleep(.01)
    wake=app.extensions.get('employee_recovery_worker')
    # The hook receives worker.wsgi, which is this wrapper. Delegate below.
    value=dict(pid=os.getpid(),expiry_threads=sum(t.name=='employee-recovery-expiry' for t in threading.enumerate()),
        wake_set=wake is not None,expiry_checks=len(set(checks)),
        heartbeat=bool(app.extensions.get('archive_delivery_heartbeat')),gc_enabled=gc.isenabled(),import_started_expiry=import_started_expiry)
    raw=json.dumps(value).encode();start_response('200 OK',[('Content-Type','application/json'),('Content-Length',str(len(raw)))])
    return [raw]

# Preserve Flask's existing interface for the real post_worker_init hook.
application.testing=app.testing
application.extensions=app.extensions
application.config=app.config
application.app_context=app.app_context
application.logger=app.logger
