import os
import sys

from app import create_app
from app.config import DevelopmentConfig


class RunEnvironmentConfig:
    NEOAPPS_ENV = os.getenv("NEOAPPS_ENV", os.getenv("FLASK_ENV", ""))


app = (
    create_app(DevelopmentConfig, auto_bootstrap=False)
    if __name__ == "__main__"
    else create_app(RunEnvironmentConfig, auto_bootstrap=False)
)

from app.services.neostaffing_employee_recovery_worker import start_recovery_worker

from app.services.neoscorpion_spear_decisions import initialize_delivery_heartbeat
# Gunicorn may import the application in its parent process. Start the existing
# background loops after worker initialization instead. In particular, an Event
# created by the recovery loop in the parent survives fork without its thread.
# Other WSGI/dev hosts keep the existing startup path. No worker count, expiry,
# transaction or resource budget changes.
if 'gunicorn' not in sys.modules:
    start_recovery_worker(app)
    initialize_delivery_heartbeat(app)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=app.config["DEBUG"])
