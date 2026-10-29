"""Use the existing web worker for archive scheduling, never Gunicorn's parent."""


def on_starting(server):
    # Collect once, before any worker exists. Frozen startup objects stay shared
    # when worker GC reclaims request/SDK cycles. Do NOT freeze on every fork:
    # that would retain retired worker objects in the master after restarts.
    # New objects still use ordinary GC. Non-preloaded hosts are unchanged.
    if server.cfg.preload_app:
        import gc
        gc.collect()
        gc.freeze()
        from app.services.memory_diagnostics import record_process_memory_checkpoint
        record_process_memory_checkpoint('gunicorn_preloaded_parent',app=server.app.wsgi())
    server.log.info('NeoApps worker startup preload=%s workers=%s',server.cfg.preload_app,server.cfg.workers)


def post_worker_init(worker):
    from app.services.neostaffing_employee_recovery_worker import start_recovery_worker
    from app.services.neoscorpion_spear_decisions import initialize_delivery_heartbeat
    start_recovery_worker(worker.wsgi)
    worker.wsgi.logger.info('NeoApps employee recovery expiry initialized in web worker pid=%s',worker.pid)
    initialize_delivery_heartbeat(worker.wsgi)
    worker.wsgi.logger.info('NeoApps archive heartbeat initialized in web worker pid=%s',worker.pid)
    from app.services.memory_diagnostics import record_process_memory_checkpoint
    record_process_memory_checkpoint('web_worker_initialized',app=worker.wsgi)
