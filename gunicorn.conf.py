"""Use the existing web worker for archive scheduling, never Gunicorn's parent."""


def post_worker_init(worker):
    from app.services.neoscorpion_spear_decisions import initialize_delivery_heartbeat
    initialize_delivery_heartbeat(worker.wsgi)
    worker.wsgi.logger.info('NeoApps archive heartbeat initialized in web worker pid=%s',worker.pid)
