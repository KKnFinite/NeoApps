import os

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
start_recovery_worker(app)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=app.config["DEBUG"])
