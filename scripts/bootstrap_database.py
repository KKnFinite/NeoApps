import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.database_bootstrap import (  # noqa: E402
    BOOTSTRAP_PASSWORD_ENV,
    bootstrap_database,
)


def main():
    result = bootstrap_database()
    password_source = (
        "local SQLite fallback"
        if result["used_fallback_password"]
        else BOOTSTRAP_PASSWORD_ENV
    )
    password_status = (
        password_source
        if result["password_applied"]
        else "existing password preserved"
    )
    print("NeoGateway database bootstrap complete.")
    print(f"Username: {result['username']}")
    print(f"Email: {result['email']}")
    print(f"Gateway: {result['gateway_code']}")
    print(f"Active NeoNodes: {result['node_count']}")
    print(f"Grandmaster node roles: {result['grandmaster_role_count']}")
    print(f"Password source: {password_status}")


if __name__ == "__main__":
    main()
