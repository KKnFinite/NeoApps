from sqlalchemy import inspect, text

from app.extensions import db


LOCAL_SQLITE_GATEWAY_COLUMNS = {
    "sort_date_operations": "gateway_id",
    "master_flight_schedules": "gateway_id",
    "crews": "gateway_id",
}

LOCAL_SQLITE_OPTIONAL_COLUMNS = {
    "users": {
        "email": "VARCHAR(255)",
        "first_name": "VARCHAR(80)",
        "last_name": "VARCHAR(80)",
        "full_name": "VARCHAR(160)",
        "employee_id": "VARCHAR(80)",
        "supervisor_name": "VARCHAR(160)",
        "work_area": "VARCHAR(160)",
        "access_reason": "TEXT",
        "email_verified_at": "DATETIME",
        "password_reset_required": "BOOLEAN DEFAULT 0",
        "password_changed_at": "DATETIME",
        "last_password_reset_by_user_id": "INTEGER",
        "last_password_reset_at": "DATETIME",
        "last_password_reset_reason": "TEXT",
    },
    "gateway_memberships": {
        "approved_by_user_id": "INTEGER",
        "approved_at": "DATETIME",
        "approval_notes": "TEXT",
        "denied_by_user_id": "INTEGER",
        "denied_at": "DATETIME",
        "denial_notes": "TEXT",
        "approval_email_sent_at": "DATETIME",
    },
    "sort_date_missions": {
        "arrival_status": "VARCHAR(32)",
        "wave": "VARCHAR(16)",
        "actual_pure_pull_time_local": "TIME",
        "actual_first_mix_pull_time_local": "TIME",
        "actual_second_mix_pull_time_local": "TIME",
        "api_status": "VARCHAR(32)",
        "api_status_raw": "VARCHAR(120)",
        "api_runway_time_utc": "DATETIME",
        "api_assumed_arrived_time_utc": "DATETIME",
        "api_aircraft_model": "VARCHAR(120)",
        "api_last_seen_at_utc": "DATETIME",
        "api_added_current_sort_only": "BOOLEAN DEFAULT 0",
    },
    "sort_date_operations": {
        "first_wave_window_minutes": "INTEGER",
        "second_wave_window_minutes": "INTEGER",
        "flight_api_last_attempted_poll_at_utc": "DATETIME",
        "flight_api_last_successful_poll_at_utc": "DATETIME",
        "flight_api_last_failed_poll_at_utc": "DATETIME",
        "flight_api_last_poll_status": "VARCHAR(32) DEFAULT ''",
        "flight_api_last_poll_summary": "VARCHAR(255) DEFAULT ''",
        "flight_api_next_auto_poll_eligible_at_utc": "DATETIME",
        "flight_api_auto_poll_in_progress_at_utc": "DATETIME",
        "flight_api_auto_poll_lock_token": "VARCHAR(64) DEFAULT ''",
        "flight_api_last_poll_snapshot_json": "TEXT",
    },
    "neosektor_wave_states": {
        "all_up_started_at": "DATETIME",
    },
    "master_flight_schedules": {
        "aircraft_type": "VARCHAR(16)",
        "wave": "VARCHAR(16)",
    },
    "sort_timeline_settings": {
        "units_per_poll": "INTEGER DEFAULT 2",
        "taxi_to_ramp_minutes": "INTEGER DEFAULT 10",
        "minimum_auto_poll_interval_minutes": "INTEGER DEFAULT 10",
    },
    "sort_timeline_usage_counters": {
        "units_consumed": "INTEGER DEFAULT 0",
    },
    "staffing_work_assignments": {
        "active": "BOOLEAN DEFAULT 1",
        "effective_date": "DATE",
    },
    "staffing_leadership_assignments": {
        "active": "BOOLEAN DEFAULT 1",
    },
    "sort_date_tail_states": {
        "operational_status": "VARCHAR(16) DEFAULT 'normal'",
        "is_out_of_service": "BOOLEAN DEFAULT 0",
    },
    "motherbrain_alerts": {
        "alert_key": "VARCHAR(160) DEFAULT ''",
        "sort_date_operation_id": "INTEGER",
    },
    "motherbrain_parking_settings": {
        "preferred_max_per_ramp": "INTEGER",
        "inbound_same_ramp_spacing_minutes": "INTEGER DEFAULT 5",
    },
    "neoermac_uld_requests": {
        "sort_date_operation_id": "INTEGER",
    },
    "neosektor_uld_on_the_way_events": {
        "sort_date_operation_id": "INTEGER",
    },
}

POSTGRES_OPTIONAL_COLUMNS = {
    "users": {
        "first_name": "VARCHAR(80)",
        "last_name": "VARCHAR(80)",
    },
    "sort_date_missions": {
        "arrival_status": "VARCHAR(32)",
        "wave": "VARCHAR(16)",
        "actual_pure_pull_time_local": "TIME",
        "actual_first_mix_pull_time_local": "TIME",
        "actual_second_mix_pull_time_local": "TIME",
        "api_status": "VARCHAR(32)",
        "api_status_raw": "VARCHAR(120)",
        "api_runway_time_utc": "TIMESTAMP",
        "api_assumed_arrived_time_utc": "TIMESTAMP",
        "api_aircraft_model": "VARCHAR(120)",
        "api_last_seen_at_utc": "TIMESTAMP",
        "api_added_current_sort_only": "BOOLEAN DEFAULT FALSE",
    },
    "sort_date_operations": {
        "first_wave_window_minutes": "INTEGER",
        "second_wave_window_minutes": "INTEGER",
        "flight_api_last_attempted_poll_at_utc": "TIMESTAMP",
        "flight_api_last_successful_poll_at_utc": "TIMESTAMP",
        "flight_api_last_failed_poll_at_utc": "TIMESTAMP",
        "flight_api_last_poll_status": "VARCHAR(32) DEFAULT ''",
        "flight_api_last_poll_summary": "VARCHAR(255) DEFAULT ''",
        "flight_api_next_auto_poll_eligible_at_utc": "TIMESTAMP",
        "flight_api_auto_poll_in_progress_at_utc": "TIMESTAMP",
        "flight_api_auto_poll_lock_token": "VARCHAR(64) DEFAULT ''",
        "flight_api_last_poll_snapshot_json": "TEXT",
    },
    "neosektor_wave_states": {
        "all_up_started_at": "TIMESTAMP",
    },
    "master_flight_schedules": {
        "aircraft_type": "VARCHAR(16)",
        "wave": "VARCHAR(16)",
    },
    "sort_timeline_settings": {
        "units_per_poll": "INTEGER DEFAULT 2",
        "taxi_to_ramp_minutes": "INTEGER DEFAULT 10",
        "minimum_auto_poll_interval_minutes": "INTEGER DEFAULT 10",
    },
    "sort_timeline_usage_counters": {
        "units_consumed": "INTEGER DEFAULT 0",
    },
    "staffing_work_assignments": {
        "active": "BOOLEAN DEFAULT TRUE",
        "effective_date": "DATE",
    },
    "staffing_leadership_assignments": {
        "active": "BOOLEAN DEFAULT TRUE",
    },
    "sort_date_tail_states": {
        "operational_status": "VARCHAR(16) DEFAULT 'normal'",
        "is_out_of_service": "BOOLEAN DEFAULT FALSE",
    },
    "motherbrain_alerts": {
        "alert_key": "VARCHAR(160) DEFAULT ''",
        "sort_date_operation_id": "INTEGER",
    },
    "motherbrain_parking_settings": {
        "preferred_max_per_ramp": "INTEGER",
        "inbound_same_ramp_spacing_minutes": "INTEGER DEFAULT 5",
    },
    "neoermac_uld_requests": {
        "sort_date_operation_id": "INTEGER",
    },
    "neosektor_uld_on_the_way_events": {
        "sort_date_operation_id": "INTEGER",
    },
}


def sync_local_sqlite_schema(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", ""))
    if not database_uri.startswith("sqlite:"):
        return

    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())
    _create_missing_application_tables(table_names)
    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())

    for table_name, column_name in LOCAL_SQLITE_GATEWAY_COLUMNS.items():
        if table_name not in table_names:
            continue

        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        if column_name in existing_columns:
            continue

        db.session.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} INTEGER"))

    for table_name, columns in LOCAL_SQLITE_OPTIONAL_COLUMNS.items():
        if table_name not in table_names:
            continue

        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name, column_type in columns.items():
            if column_name in existing_columns:
                continue

            db.session.execute(
                text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
            )

    _sync_sort_date_mission_status_constraints_sqlite(inspector, table_names)
    _sync_uld_request_unique_constraint_sqlite(inspector, table_names)
    db.session.flush()


def sync_database_schema(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", ""))
    if database_uri.startswith("sqlite:"):
        sync_local_sqlite_schema(app)
        return

    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())
    _create_missing_application_tables(table_names)
    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())

    for table_name, columns in POSTGRES_OPTIONAL_COLUMNS.items():
        if table_name not in table_names:
            continue

        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name, column_type in columns.items():
            if column_name in existing_columns:
                continue

            db.session.execute(
                text(
                    f"ALTER TABLE {table_name} "
                    f"ADD COLUMN IF NOT EXISTS {column_name} {column_type}"
                )
            )

    _sync_sort_date_mission_status_constraints_postgres(table_names)
    _sync_uld_request_unique_constraint_postgres(table_names)
    db.session.flush()


def _sync_sort_date_mission_status_constraints_sqlite(inspector, table_names):
    table_name = "sort_date_missions"
    legacy_table = "sort_date_missions_status_legacy"
    all_tables = set(inspector.get_table_names())
    if table_name not in table_names:
        return

    create_sql = db.session.execute(
        text(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'sort_date_missions'"
        )
    ).scalar() or ""
    if "'cancelled'" in create_sql:
        return

    if legacy_table in all_tables:
        db.session.execute(text(f"DROP TABLE {legacy_table}"))

    from app.models import SortDateMission

    db.session.execute(text("PRAGMA legacy_alter_table=ON"))
    db.session.execute(text(f"ALTER TABLE {table_name} RENAME TO {legacy_table}"))
    _drop_sqlite_indexes_for_table(legacy_table)
    SortDateMission.__table__.create(bind=db.engine, checkfirst=True)

    legacy_columns = {
        row[1]
        for row in db.session.execute(text(f"PRAGMA table_info({legacy_table})")).all()
    }
    copy_columns = [
        column.name
        for column in SortDateMission.__table__.columns
        if column.name in legacy_columns
    ]
    quoted_columns = ", ".join(_quote_sqlite_identifier(column) for column in copy_columns)
    db.session.execute(
        text(
            f"INSERT INTO {table_name} ({quoted_columns}) "
            f"SELECT {quoted_columns} FROM {legacy_table}"
        )
    )
    db.session.execute(text(f"DROP TABLE {legacy_table}"))
    db.session.execute(text("PRAGMA legacy_alter_table=OFF"))


def _sync_sort_date_mission_status_constraints_postgres(table_names):
    if "sort_date_missions" not in table_names:
        return

    db.session.execute(
        text(
            "ALTER TABLE sort_date_missions "
            "DROP CONSTRAINT IF EXISTS ck_sort_date_missions_arrival_status"
        )
    )
    db.session.execute(
        text(
            """
            ALTER TABLE sort_date_missions
            ADD CONSTRAINT ck_sort_date_missions_arrival_status
            CHECK (
                arrival_status IS NULL OR arrival_status IN (
                    'scheduled',
                    'en_route',
                    'arrived',
                    'unloaded',
                    'cancelled'
                )
            )
            """
        )
    )
    db.session.execute(
        text(
            "ALTER TABLE sort_date_missions "
            "DROP CONSTRAINT IF EXISTS ck_sort_date_missions_departure_status"
        )
    )
    db.session.execute(
        text(
            """
            ALTER TABLE sort_date_missions
            ADD CONSTRAINT ck_sort_date_missions_departure_status
            CHECK (
                departure_status IS NULL OR departure_status IN (
                    'loading',
                    'last_uld_enroute',
                    'ramp_load_complete',
                    'crew_load_complete',
                    'blocked_out',
                    'cancelled'
                )
            )
            """
        )
    )


def _drop_sqlite_indexes_for_table(table_name):
    for row in db.session.execute(text(f"PRAGMA index_list({table_name})")).all():
        index_name = row[1]
        if str(index_name).startswith("sqlite_autoindex"):
            continue
        db.session.execute(
            text(f"DROP INDEX IF EXISTS {_quote_sqlite_identifier(index_name)}")
        )


def _quote_sqlite_identifier(value):
    return '"' + str(value).replace('"', '""') + '"'


def _sync_uld_request_unique_constraint_sqlite(inspector, table_names):
    table_name = "neoermac_uld_requests"
    legacy_table = "neoermac_uld_requests_legacy"
    all_tables = set(inspector.get_table_names())
    if table_name not in all_tables and legacy_table not in all_tables:
        return

    if legacy_table in all_tables:
        _restore_uld_request_sqlite_table_from_legacy(table_name, legacy_table)
        return

    unique_sets = {
        tuple(constraint.get("column_names") or ())
        for constraint in inspector.get_unique_constraints(table_name)
    }
    if ("gateway_id", "sort_date_operation_id", "door", "setup_needed") in unique_sets:
        return

    from app.models import NeoErmacUldRequest

    db.session.execute(text(f"ALTER TABLE {table_name} RENAME TO {legacy_table}"))
    _drop_uld_request_sqlite_indexes()
    NeoErmacUldRequest.__table__.create(bind=db.engine, checkfirst=True)
    _copy_uld_request_legacy_rows()
    db.session.execute(text(f"DROP TABLE {legacy_table}"))


def _restore_uld_request_sqlite_table_from_legacy(table_name, legacy_table):
    from app.models import NeoErmacUldRequest

    _drop_uld_request_sqlite_indexes()
    NeoErmacUldRequest.__table__.create(bind=db.engine, checkfirst=True)
    _copy_uld_request_legacy_rows()
    db.session.execute(text(f"DROP TABLE {legacy_table}"))


def _drop_uld_request_sqlite_indexes():
    db.session.execute(text("DROP INDEX IF EXISTS ix_neoermac_uld_requests_door"))
    db.session.execute(text("DROP INDEX IF EXISTS ix_neoermac_uld_requests_gateway_id"))
    db.session.execute(
        text("DROP INDEX IF EXISTS ix_neoermac_uld_requests_sort_date_operation_id")
    )


def _copy_uld_request_legacy_rows():
    _ensure_sqlite_column(
        "neoermac_uld_requests_legacy",
        "sort_date_operation_id",
        "INTEGER",
    )
    db.session.execute(
        text(
            """
            INSERT OR IGNORE INTO neoermac_uld_requests (
                id,
                gateway_id,
                sort_date_operation_id,
                door,
                a2_count,
                a1_count,
                amp_count,
                setup_needed,
                created_at,
                updated_at
            )
            SELECT
                id,
                gateway_id,
                sort_date_operation_id,
                door,
                a2_count,
                a1_count,
                amp_count,
                setup_needed,
                created_at,
                updated_at
            FROM neoermac_uld_requests_legacy
            """
        )
    )


def _ensure_sqlite_column(table_name, column_name, column_type):
    existing_columns = {
        row[1] for row in db.session.execute(text(f"PRAGMA table_info({table_name})")).all()
    }
    if column_name in existing_columns:
        return
    db.session.execute(
        text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
    )


def _sync_uld_request_unique_constraint_postgres(table_names):
    if "neoermac_uld_requests" not in table_names:
        return

    db.session.execute(
        text(
            "ALTER TABLE neoermac_uld_requests "
            "DROP CONSTRAINT IF EXISTS uq_neoermac_uld_requests_gateway_door"
        )
    )
    db.session.execute(
        text(
            "ALTER TABLE neoermac_uld_requests "
            "DROP CONSTRAINT IF EXISTS uq_neoermac_uld_requests_gateway_door_setup"
        )
    )
    db.session.execute(
        text(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'uq_neoermac_uld_requests_gateway_operation_door_setup'
                ) THEN
                    ALTER TABLE neoermac_uld_requests
                    ADD CONSTRAINT uq_neoermac_uld_requests_gateway_operation_door_setup
                    UNIQUE (gateway_id, sort_date_operation_id, door, setup_needed);
                END IF;
            END
            $$;
            """
        )
    )


def _create_missing_application_tables(existing_table_names):
    from app.models import (
        FlightApiReviewItem,
        MotherBrainAlert,
        MotherBrainParkingRule,
        MotherBrainParkingSettings,
        NeoScorpionFuelAssignment,
        NeoScorpionFuelTruck,
        NeoScorpionSettings,
        NeoScorpionTailFuelState,
        PortalAppAccess,
        SortDateParkingAssignment,
        StaffingLeadershipAssignment,
        StaffingPerson,
        StaffingUnit,
        StaffingWorkAssignment,
        SortTimelineApiParticipation,
        SortTimelineMonthVariance,
        SortTimelineSettings,
        SortTimelineSortSetting,
        SortTimelineSpecialPollTime,
        SortTimelineUsageCounter,
    )

    for model in (
        FlightApiReviewItem,
        MotherBrainAlert,
        MotherBrainParkingRule,
        MotherBrainParkingSettings,
        NeoScorpionTailFuelState,
        NeoScorpionFuelTruck,
        NeoScorpionFuelAssignment,
        NeoScorpionSettings,
        PortalAppAccess,
        SortDateParkingAssignment,
        StaffingPerson,
        StaffingUnit,
        StaffingWorkAssignment,
        StaffingLeadershipAssignment,
        SortTimelineSettings,
        SortTimelineApiParticipation,
        SortTimelineMonthVariance,
        SortTimelineSortSetting,
        SortTimelineSpecialPollTime,
        SortTimelineUsageCounter,
    ):
        if model.__tablename__ in existing_table_names:
            continue
        model.__table__.create(bind=db.engine, checkfirst=True)
