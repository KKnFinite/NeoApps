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
        "is_management": "BOOLEAN DEFAULT 0",
        "management_level": "VARCHAR(40)",
        "access_reason": "TEXT",
        "email_verified_at": "DATETIME",
        "password_reset_required": "BOOLEAN DEFAULT 0",
        "password_policy_update_required": "BOOLEAN DEFAULT 0",
        "auth_session_version": "INTEGER NOT NULL DEFAULT 1",
        "password_changed_at": "DATETIME",
        "temporary_password_expires_at": "DATETIME",
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
        "mix_pull_time_local": "TIME",
        "actual_mix_pull_time_local": "TIME",
        "api_status": "VARCHAR(32)",
        "api_status_raw": "VARCHAR(120)",
        "api_runway_time_utc": "DATETIME",
        "api_assumed_arrived_time_utc": "DATETIME",
        "api_aircraft_model": "VARCHAR(120)",
        "api_last_seen_at_utc": "DATETIME",
        "api_added_current_sort_only": "BOOLEAN DEFAULT 0",
        "elmac_completed_at_utc": "DATETIME",
        "elmac_completed_source": "VARCHAR(32) NOT NULL DEFAULT 'unknown'",
        "ramp_load_completed_source": "VARCHAR(32) NOT NULL DEFAULT 'unknown'",
        "crew_load_completed_source": "VARCHAR(32) NOT NULL DEFAULT 'unknown'",
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
    "neosektor_operational_settings": {
        "google_sheets_compat_enabled": "BOOLEAN NOT NULL DEFAULT 0",
        "last_google_read_at_utc": "DATETIME",
        "integration_mode": "VARCHAR(40) NOT NULL DEFAULT 'google_primary'",
        "google_mirror_sync_needed": "BOOLEAN NOT NULL DEFAULT 0",
        "google_mirror_last_error": "VARCHAR(255)",
        "google_mirror_failed_at_utc": "DATETIME",
    },
    "master_flight_schedules": {
        "aircraft_type": "VARCHAR(16)",
        "wave": "VARCHAR(16)",
        "mix_pull_time_local": "TIME",
    },
    "neoermac_door_pulls": {
        "sort_date_operation_id": "INTEGER",
        "actual_pure_pull_time_local": "TIME",
        "no_pure_pull": "BOOLEAN NOT NULL DEFAULT 0",
        "actual_mix_pull_time_local": "TIME",
        "no_mix_pull": "BOOLEAN NOT NULL DEFAULT 0",
        "created_at": "DATETIME",
        "updated_at": "DATETIME",
    },
    "neoermac_building_lineups": {
        "east_destination_1_slot_2": "VARCHAR(8)",
        "east_destination_2_slot_2": "VARCHAR(8)",
        "west_destination_1_slot_2": "VARCHAR(8)",
        "west_destination_2_slot_2": "VARCHAR(8)",
    },
    "sort_timeline_settings": {
        "units_per_poll": "INTEGER DEFAULT 2",
        "taxi_to_ramp_minutes": "INTEGER DEFAULT 10",
        "minimum_auto_poll_interval_minutes": "INTEGER DEFAULT 10",
    },
    "sort_timeline_sort_settings": {
        "planning_start_local": "TIME",
        "google_polling_start_local": "TIME",
        "google_polling_end_local": "TIME",
    },
    "sort_timeline_usage_counters": {
        "units_consumed": "INTEGER DEFAULT 0",
    },
    "staffing_work_assignments": {
        "active": "BOOLEAN DEFAULT 1",
        "effective_date": "DATE",
    },
    "staffing_people": {
        "employee_status": "VARCHAR(24)",
    },
    "staffing_leadership_assignments": {
        "active": "BOOLEAN DEFAULT 1",
    },
    "staffing_daily_attendance": {
        "sort_date_operation_id": "INTEGER",
        "department_unit_id": "INTEGER",
        "operation_unit_id": "INTEGER",
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
        "prevent_767_adjacent_to_a300": "BOOLEAN NOT NULL DEFAULT 1",
        "force_767_to_position_4_8": "BOOLEAN NOT NULL DEFAULT 1",
        "prevent_a300_in_position_5": "BOOLEAN NOT NULL DEFAULT 1",
    },
    "neoermac_uld_requests": {
        "sort_date_operation_id": "INTEGER",
        "requested_by_user_id": "INTEGER",
    },
    "neosektor_uld_on_the_way_events": {
        "sort_date_operation_id": "INTEGER",
        "requested_by_user_id": "INTEGER",
    },
    "neoscorpion_fuel_work_states": {
        "apu_running": "BOOLEAN",
        "apu_confirmed_at_utc": "DATETIME",
        "apu_allowance_lbs": "INTEGER",
        "applied_apu_rate_thousand_lbs_per_hour": "NUMERIC(8, 4)",
        "apu_source_tank_code": "VARCHAR(32)",
        "off_at_utc": "DATETIME",
        "off_by_user_id": "INTEGER",
        "truck_segment_started_at_utc": "DATETIME",
        "ended_early_at_utc": "DATETIME",
        "ended_early_by_user_id": "INTEGER",
        "ended_early_reason": "TEXT",
    },
    "neoscorpion_settings": {
        "planning_inbound_fuel_fallback_lbs": "INTEGER",
    },
    "neoscorpion_fuel_assignments": {
        "fuel_on_board_at_utc": "DATETIME",
        "fuel_on_board_by_user_id": "INTEGER",
        "completed_at_utc": "DATETIME",
        "completed_by_user_id": "INTEGER",
        "confirmed_tail_number": "VARCHAR(32)",
        "operational_status": "VARCHAR(32) NOT NULL DEFAULT 'active'",
        "hold_reason": "TEXT",
        "hold_at_utc": "DATETIME",
        "hold_by_user_id": "INTEGER",
    },
}

POSTGRES_OPTIONAL_COLUMNS = {
    "users": {
        "first_name": "VARCHAR(80)",
        "last_name": "VARCHAR(80)",
        "is_management": "BOOLEAN DEFAULT FALSE",
        "management_level": "VARCHAR(40)",
        "password_policy_update_required": "BOOLEAN DEFAULT FALSE",
        "auth_session_version": "INTEGER NOT NULL DEFAULT 1",
        "temporary_password_expires_at": "TIMESTAMP WITH TIME ZONE",
    },
    "sort_date_missions": {
        "arrival_status": "VARCHAR(32)",
        "wave": "VARCHAR(16)",
        "actual_pure_pull_time_local": "TIME",
        "mix_pull_time_local": "TIME",
        "actual_mix_pull_time_local": "TIME",
        "api_status": "VARCHAR(32)",
        "api_status_raw": "VARCHAR(120)",
        "api_runway_time_utc": "TIMESTAMP",
        "api_assumed_arrived_time_utc": "TIMESTAMP",
        "api_aircraft_model": "VARCHAR(120)",
        "api_last_seen_at_utc": "TIMESTAMP",
        "api_added_current_sort_only": "BOOLEAN DEFAULT FALSE",
        "elmac_completed_at_utc": "TIMESTAMP",
        "elmac_completed_source": "VARCHAR(32) NOT NULL DEFAULT 'unknown'",
        "ramp_load_completed_source": "VARCHAR(32) NOT NULL DEFAULT 'unknown'",
        "crew_load_completed_source": "VARCHAR(32) NOT NULL DEFAULT 'unknown'",
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
    "neosektor_operational_settings": {
        "google_sheets_compat_enabled": "BOOLEAN NOT NULL DEFAULT FALSE",
        "last_google_read_at_utc": "TIMESTAMP",
        "integration_mode": "VARCHAR(40) NOT NULL DEFAULT 'google_primary'",
        "google_mirror_sync_needed": "BOOLEAN NOT NULL DEFAULT FALSE",
        "google_mirror_last_error": "VARCHAR(255)",
        "google_mirror_failed_at_utc": "TIMESTAMP",
    },
    "master_flight_schedules": {
        "aircraft_type": "VARCHAR(16)",
        "wave": "VARCHAR(16)",
        "mix_pull_time_local": "TIME",
    },
    "neoermac_door_pulls": {
        "sort_date_operation_id": "INTEGER",
        "actual_pure_pull_time_local": "TIME",
        "no_pure_pull": "BOOLEAN NOT NULL DEFAULT FALSE",
        "actual_mix_pull_time_local": "TIME",
        "no_mix_pull": "BOOLEAN NOT NULL DEFAULT FALSE",
        "created_at": "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "updated_at": "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
    },
    "neoermac_building_lineups": {
        "east_destination_1_slot_2": "VARCHAR(8)",
        "east_destination_2_slot_2": "VARCHAR(8)",
        "west_destination_1_slot_2": "VARCHAR(8)",
        "west_destination_2_slot_2": "VARCHAR(8)",
    },
    "sort_timeline_settings": {
        "units_per_poll": "INTEGER DEFAULT 2",
        "taxi_to_ramp_minutes": "INTEGER DEFAULT 10",
        "minimum_auto_poll_interval_minutes": "INTEGER DEFAULT 10",
    },
    "sort_timeline_sort_settings": {
        "planning_start_local": "TIME",
        "google_polling_start_local": "TIME",
        "google_polling_end_local": "TIME",
    },
    "sort_timeline_usage_counters": {
        "units_consumed": "INTEGER DEFAULT 0",
    },
    "staffing_work_assignments": {
        "active": "BOOLEAN DEFAULT TRUE",
        "effective_date": "DATE",
    },
    "staffing_people": {
        "employee_status": "VARCHAR(24)",
    },
    "staffing_leadership_assignments": {
        "active": "BOOLEAN DEFAULT TRUE",
    },
    "staffing_daily_attendance": {
        "sort_date_operation_id": "INTEGER",
        "department_unit_id": "INTEGER",
        "operation_unit_id": "INTEGER",
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
        "prevent_767_adjacent_to_a300": "BOOLEAN NOT NULL DEFAULT TRUE",
        "force_767_to_position_4_8": "BOOLEAN NOT NULL DEFAULT TRUE",
        "prevent_a300_in_position_5": "BOOLEAN NOT NULL DEFAULT TRUE",
    },
    "neoermac_uld_requests": {
        "sort_date_operation_id": "INTEGER",
        "requested_by_user_id": "INTEGER",
    },
    "neosektor_uld_on_the_way_events": {
        "sort_date_operation_id": "INTEGER",
        "requested_by_user_id": "INTEGER",
    },
    "neoscorpion_fuel_work_states": {
        "apu_running": "BOOLEAN",
        "apu_confirmed_at_utc": "TIMESTAMP",
        "apu_allowance_lbs": "INTEGER",
        "applied_apu_rate_thousand_lbs_per_hour": "NUMERIC(8, 4)",
        "apu_source_tank_code": "VARCHAR(32)",
        "off_at_utc": "TIMESTAMP",
        "off_by_user_id": "INTEGER",
        "truck_segment_started_at_utc": "TIMESTAMP",
        "ended_early_at_utc": "TIMESTAMP",
        "ended_early_by_user_id": "INTEGER",
        "ended_early_reason": "TEXT",
    },
    "neoscorpion_settings": {
        "planning_inbound_fuel_fallback_lbs": "INTEGER",
    },
    "neoscorpion_fuel_assignments": {
        "fuel_on_board_at_utc": "TIMESTAMP",
        "fuel_on_board_by_user_id": "INTEGER",
        "completed_at_utc": "TIMESTAMP",
        "completed_by_user_id": "INTEGER",
        "confirmed_tail_number": "VARCHAR(32)",
        "operational_status": "VARCHAR(32) NOT NULL DEFAULT 'active'",
        "hold_reason": "TEXT",
        "hold_at_utc": "TIMESTAMP",
        "hold_by_user_id": "INTEGER",
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
    migrate_existing_approved_users = _users_missing_password_policy_column(
        inspector,
        table_names,
    )

    table_columns = {
        table_name: {column["name"] for column in inspector.get_columns(table_name)}
        for table_name in table_names
    }

    for table_name, column_name in LOCAL_SQLITE_GATEWAY_COLUMNS.items():
        if table_name not in table_names:
            continue

        existing_columns = table_columns[table_name]
        if column_name in existing_columns:
            continue

        db.session.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} INTEGER"))
        existing_columns.add(column_name)

    for table_name, columns in LOCAL_SQLITE_OPTIONAL_COLUMNS.items():
        if table_name not in table_names:
            continue

        existing_columns = table_columns[table_name]
        for column_name, column_type in columns.items():
            if column_name in existing_columns:
                continue

            db.session.execute(
                text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
            )
            existing_columns.add(column_name)

    _sync_staffing_people_employee_status_sqlite(table_names)
    _sync_sort_date_mission_status_constraints_sqlite(inspector, table_names)
    _create_google_mission_link_table()
    _create_google_live_poll_state_table()
    rebuilt_constraint_table = _sync_sort_date_tail_state_status_constraints_sqlite(
        inspector,
        table_names,
    )
    rebuilt_constraint_table = (
        _sync_neoscorpion_fuel_audit_actions_sqlite(inspector, table_names)
        or rebuilt_constraint_table
    )
    if rebuilt_constraint_table:
        db.session.commit()
        inspector = inspect(db.engine)
        table_names = set(inspector.get_table_names())
        table_columns = {
            table_name: {column["name"] for column in inspector.get_columns(table_name)}
            for table_name in table_names
        }
    _sync_uld_request_unique_constraint_sqlite(inspector, table_names)
    _backfill_motherbrain_parking_rule_defaults(table_names, table_columns)
    if migrate_existing_approved_users:
        _mark_existing_approved_users_for_password_policy_update(table_names)
    _validate_neoermac_door_pull_schema(table_names, table_columns)
    _backfill_neoermac_door_pull_timestamps(table_names, table_columns)
    _migrate_legacy_second_mix_pull_values(table_names, table_columns)
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
    migrate_existing_approved_users = _users_missing_password_policy_column(
        inspector,
        table_names,
    )

    table_columns = {
        table_name: {column["name"] for column in inspector.get_columns(table_name)}
        for table_name in table_names
    }

    for table_name, columns in POSTGRES_OPTIONAL_COLUMNS.items():
        if table_name not in table_names:
            continue

        existing_columns = table_columns[table_name]
        for column_name, column_type in columns.items():
            if column_name in existing_columns:
                continue

            db.session.execute(
                text(
                    f"ALTER TABLE {table_name} "
                    f"ADD COLUMN IF NOT EXISTS {column_name} {column_type}"
                )
            )
            existing_columns.add(column_name)

    _sync_staffing_people_employee_status_postgres(table_names)
    _sync_sort_date_mission_status_constraints_postgres(table_names)
    _create_google_mission_link_table()
    _create_google_live_poll_state_table()
    _sync_sort_date_tail_state_status_constraints_postgres(table_names)
    _sync_neoscorpion_fuel_audit_actions_postgres(table_names)
    _sync_uld_request_unique_constraint_postgres(table_names)
    _backfill_motherbrain_parking_rule_defaults(table_names, table_columns)
    if migrate_existing_approved_users:
        _mark_existing_approved_users_for_password_policy_update(table_names)
    _validate_neoermac_door_pull_schema(table_names, table_columns)
    _backfill_neoermac_door_pull_timestamps(table_names, table_columns)
    _migrate_legacy_second_mix_pull_values(table_names, table_columns)
    db.session.flush()


def _backfill_motherbrain_parking_rule_defaults(table_names, table_columns):
    table_name = "motherbrain_parking_settings"
    if table_name not in table_names:
        return

    existing_columns = table_columns.get(table_name, set())
    for column_name in (
        "prevent_767_adjacent_to_a300",
        "force_767_to_position_4_8",
        "prevent_a300_in_position_5",
    ):
        if column_name not in existing_columns:
            continue
        db.session.execute(
            text(
                f"UPDATE {table_name} SET {column_name} = TRUE "
                f"WHERE {column_name} IS NULL"
            )
        )


def _validate_neoermac_door_pull_schema(table_names, table_columns):
    table_name = "neoermac_door_pulls"
    if table_name not in table_names:
        return

    from app.models import NeoErmacDoorPull

    expected_columns = {column.name for column in NeoErmacDoorPull.__table__.columns}
    missing_columns = expected_columns - table_columns.get(table_name, set())
    if missing_columns:
        missing_list = ", ".join(sorted(missing_columns))
        raise RuntimeError(
            "NeoErmac Door Pull schema is missing required foundational columns: "
            f"{missing_list}. Manual database repair is required before bootstrap."
        )


def _backfill_neoermac_door_pull_timestamps(table_names, table_columns):
    table_name = "neoermac_door_pulls"
    if table_name not in table_names:
        return

    existing_columns = table_columns.get(table_name, set())
    for column_name in ("created_at", "updated_at"):
        if column_name not in existing_columns:
            continue
        db.session.execute(
            text(
                f"UPDATE {table_name} "
                f"SET {column_name} = CURRENT_TIMESTAMP "
                f"WHERE {column_name} IS NULL"
            )
        )


def _migrate_legacy_second_mix_pull_values(table_names, table_columns=None):
    """Copy the retired 2nd Mix values into the new Mix Pull columns once."""
    time_migrations = (
        (
            "master_flight_schedules",
            "mix_pull_time_local",
            "final_mix_pull_time_local",
        ),
        (
            "sort_date_missions",
            "mix_pull_time_local",
            "final_mix_pull_time_local",
        ),
        (
            "sort_date_missions",
            "actual_mix_pull_time_local",
            "actual_second_mix_pull_time_local",
        ),
        (
            "neoermac_door_pulls",
            "actual_mix_pull_time_local",
            "actual_second_mix_pull_time_local",
        ),
    )
    boolean_migrations = (
        (
            "neoermac_door_pulls",
            "no_mix_pull",
            "no_second_mix_pull",
        ),
    )

    if table_columns is None:
        inspector = inspect(db.engine)
        table_columns = {
            table_name: {
                column["name"] for column in inspector.get_columns(table_name)
            }
            for table_name in table_names
        }

    for table_name, target_column, legacy_column in time_migrations:
        if table_name not in table_names:
            continue
        existing_columns = table_columns.get(table_name, set())
        if {target_column, legacy_column}.issubset(existing_columns):
            db.session.execute(
                text(
                    f"UPDATE {table_name} "
                    f"SET {target_column} = {legacy_column} "
                    f"WHERE {target_column} IS NULL AND {legacy_column} IS NOT NULL"
                )
            )

    for table_name, target_column, legacy_column in boolean_migrations:
        if table_name not in table_names:
            continue
        existing_columns = table_columns.get(table_name, set())
        if {target_column, legacy_column}.issubset(existing_columns):
            db.session.execute(
                text(
                    f"UPDATE {table_name} "
                    f"SET {target_column} = TRUE "
                    f"WHERE {legacy_column} IS TRUE "
                    f"AND COALESCE({target_column}, FALSE) IS FALSE"
                )
            )


def _users_missing_password_policy_column(inspector, table_names):
    if "users" not in table_names:
        return False

    existing_columns = {column["name"] for column in inspector.get_columns("users")}
    return "password_policy_update_required" not in existing_columns


def _mark_existing_approved_users_for_password_policy_update(table_names):
    approved_user_queries = []
    if "gateway_memberships" in table_names:
        approved_user_queries.append(
            "SELECT user_id FROM gateway_memberships "
            "WHERE status = 'approved' AND is_active IS TRUE"
        )
    if "portal_app_accesses" in table_names:
        approved_user_queries.append(
            "SELECT user_id FROM portal_app_accesses "
            "WHERE status = 'approved' AND is_active IS TRUE"
        )
    if not approved_user_queries:
        return

    db.session.execute(
        text(
            "UPDATE users SET password_policy_update_required = TRUE "
            f"WHERE id IN ({' UNION '.join(approved_user_queries)})"
        )
    )


def _sync_staffing_people_employee_status_sqlite(table_names):
    if "staffing_people" not in table_names:
        return

    existing_columns = {
        row[1] for row in db.session.execute(text("PRAGMA table_info(staffing_people)")).all()
    }
    if "employee_status" not in existing_columns:
        return

    if "roster_status" in existing_columns:
        db.session.execute(
            text(
                """
                UPDATE staffing_people
                SET employee_status = COALESCE(NULLIF(employee_status, ''), roster_status, 'active')
                WHERE employee_status IS NULL OR employee_status = ''
                """
            )
        )
        return

    db.session.execute(
        text(
            """
            UPDATE staffing_people
            SET employee_status = 'active'
            WHERE employee_status IS NULL OR employee_status = ''
            """
        )
    )


def _sync_staffing_people_employee_status_postgres(table_names):
    if "staffing_people" not in table_names:
        return

    column_names = {
        row[0]
        for row in db.session.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'staffing_people'
                """
            )
        ).all()
    }
    if "employee_status" not in column_names:
        return

    if "roster_status" in column_names:
        db.session.execute(
            text(
                """
                UPDATE staffing_people
                SET employee_status = COALESCE(NULLIF(employee_status, ''), roster_status, 'active')
                WHERE employee_status IS NULL OR employee_status = ''
                """
            )
        )
        return

    db.session.execute(
        text(
            """
            UPDATE staffing_people
            SET employee_status = 'active'
            WHERE employee_status IS NULL OR employee_status = ''
            """
        )
    )


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
    mission_columns = {
        column["name"]: column for column in inspector.get_columns(table_name)
    }
    planned_columns_nullable = all(
        mission_columns.get(column_name, {}).get("nullable", False)
        for column_name in ("planned_datetime_local", "planned_datetime_utc")
    )
    if (
        "'cancelled'" in create_sql
        and "'on_ground'" in create_sql
        and "'departed'" in create_sql
        and "departure_status IN ('scheduled', 'loading'" in create_sql
        and "'google_motherbrain'" in create_sql
        and planned_columns_nullable
    ):
        return

    if legacy_table in all_tables:
        db.session.execute(text(f"DROP TABLE {legacy_table}"))

    from app.models import SortDateMission

    db.session.execute(text("PRAGMA legacy_alter_table=ON"))
    db.session.execute(text(f"ALTER TABLE {table_name} RENAME TO {legacy_table}"))
    _drop_sqlite_indexes_for_table(legacy_table)
    SortDateMission.__table__.create(bind=db.session.connection(), checkfirst=False)

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
            "ALTER COLUMN planned_datetime_local DROP NOT NULL"
        )
    )
    db.session.execute(
        text(
            "ALTER TABLE sort_date_missions "
            "ALTER COLUMN planned_datetime_utc DROP NOT NULL"
        )
    )

    db.session.execute(
        text(
            "ALTER TABLE sort_date_missions "
            "DROP CONSTRAINT IF EXISTS ck_sort_date_missions_mission_source"
        )
    )
    db.session.execute(
        text(
            """
            ALTER TABLE sort_date_missions
            ADD CONSTRAINT ck_sort_date_missions_mission_source
            CHECK (
                mission_source IN (
                    'master',
                    'api',
                    'manual',
                    'google_motherbrain'
                )
            )
            """
        )
    )

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
                    'on_ground',
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
                    'scheduled',
                    'loading',
                    'last_uld_enroute',
                    'ramp_load_complete',
                    'crew_load_complete',
                    'blocked_out',
                    'departed',
                    'cancelled'
                )
            )
            """
        )
    )


def _sync_sort_date_tail_state_status_constraints_sqlite(inspector, table_names):
    table_name = "sort_date_tail_states"
    legacy_table = "sort_date_tail_states_status_legacy"
    all_tables = set(inspector.get_table_names())
    if table_name not in table_names:
        return False

    create_sql = db.session.execute(
        text(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'sort_date_tail_states'"
        )
    ).scalar() or ""
    if "'spare'" in create_sql and "'qt'" in create_sql and "'oos'" in create_sql:
        return False

    if legacy_table in all_tables:
        db.session.execute(text(f"DROP TABLE {legacy_table}"))

    from app.models import SortDateTailState

    db.session.execute(text("PRAGMA legacy_alter_table=ON"))
    db.session.execute(text(f"ALTER TABLE {table_name} RENAME TO {legacy_table}"))
    _drop_sqlite_indexes_for_table(legacy_table)
    SortDateTailState.__table__.create(bind=db.session.connection(), checkfirst=False)

    legacy_columns = {
        row[1]
        for row in db.session.execute(text(f"PRAGMA table_info({legacy_table})")).all()
    }
    target_columns = []
    select_expressions = []
    fallback_expressions = {
        "aircraft_type_source": "'unknown'",
        "mechanical_status": "0",
        "operational_status": "'normal'",
        "is_out_of_service": "0",
        "pushback_status": "0",
        "deice_status": "'unknown'",
        "pretreat_status": "0",
        "created_at": "CURRENT_TIMESTAMP",
        "updated_at": "CURRENT_TIMESTAMP",
    }
    for column in SortDateTailState.__table__.columns:
        column_name = column.name
        fallback = fallback_expressions.get(column_name)
        if column_name in legacy_columns:
            quoted_column = _quote_sqlite_identifier(column_name)
            expression = quoted_column
            if fallback:
                expression = f"COALESCE({quoted_column}, {fallback})"
        elif fallback:
            expression = fallback
        elif column.nullable:
            expression = "NULL"
        else:
            continue
        target_columns.append(column_name)
        select_expressions.append(expression)

    quoted_columns = ", ".join(_quote_sqlite_identifier(column) for column in target_columns)
    select_columns = ", ".join(select_expressions)
    db.session.execute(
        text(
            f"INSERT INTO {table_name} ({quoted_columns}) "
            f"SELECT {select_columns} FROM {legacy_table}"
        )
    )
    db.session.execute(text(f"DROP TABLE {legacy_table}"))
    db.session.execute(text("PRAGMA legacy_alter_table=OFF"))
    return True


def _sync_sort_date_tail_state_status_constraints_postgres(table_names):
    if "sort_date_tail_states" not in table_names:
        return

    db.session.execute(
        text(
            "ALTER TABLE sort_date_tail_states "
            "DROP CONSTRAINT IF EXISTS ck_sort_date_tail_states_operational_status"
        )
    )
    db.session.execute(
        text(
            """
            ALTER TABLE sort_date_tail_states
            ADD CONSTRAINT ck_sort_date_tail_states_operational_status
            CHECK (
                operational_status IN (
                    'normal',
                    'hot',
                    'spare',
                    'qt',
                    'oos'
                )
            )
            """
        )
    )


def _sync_neoscorpion_fuel_audit_actions_sqlite(inspector, table_names):
    table_name = "neoscorpion_fuel_audit_entries"
    legacy_table = "neoscorpion_fuel_audit_entries_action_legacy"
    if table_name not in table_names:
        return False
    create_sql = db.session.execute(
        text(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'neoscorpion_fuel_audit_entries'"
        )
    ).scalar() or ""
    if "'end_early'" in create_sql and "'auto_hold'" in create_sql:
        return False

    all_tables = set(inspector.get_table_names())
    if legacy_table in all_tables:
        db.session.execute(text(f"DROP TABLE {legacy_table}"))
    from app.models import NeoScorpionFuelAuditEntry

    db.session.execute(text("PRAGMA legacy_alter_table=ON"))
    db.session.execute(text(f"ALTER TABLE {table_name} RENAME TO {legacy_table}"))
    _drop_sqlite_indexes_for_table(legacy_table)
    NeoScorpionFuelAuditEntry.__table__.create(
        bind=db.session.connection(),
        checkfirst=False,
    )
    legacy_columns = {
        row[1]
        for row in db.session.execute(text(f"PRAGMA table_info({legacy_table})")).all()
    }
    copy_columns = [
        column.name
        for column in NeoScorpionFuelAuditEntry.__table__.columns
        if column.name in legacy_columns
    ]
    quoted_columns = ", ".join(
        _quote_sqlite_identifier(column) for column in copy_columns
    )
    db.session.execute(
        text(
            f"INSERT INTO {table_name} ({quoted_columns}) "
            f"SELECT {quoted_columns} FROM {legacy_table}"
        )
    )
    db.session.execute(text(f"DROP TABLE {legacy_table}"))
    db.session.execute(text("PRAGMA legacy_alter_table=OFF"))
    return True


def _sync_neoscorpion_fuel_audit_actions_postgres(table_names):
    if "neoscorpion_fuel_audit_entries" not in table_names:
        return
    constraint_definition = db.session.execute(
        text(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname = 'ck_neoscorpion_fuel_audit_entry_action'
              AND conrelid = 'neoscorpion_fuel_audit_entries'::regclass
            """
        )
    ).scalar()
    if (
        constraint_definition
        and "auto_hold" in constraint_definition
        and "end_early" in constraint_definition
    ):
        return
    db.session.execute(
        text(
            "ALTER TABLE neoscorpion_fuel_audit_entries "
            "DROP CONSTRAINT IF EXISTS ck_neoscorpion_fuel_audit_entry_action"
        )
    )
    db.session.execute(
        text(
            """
            ALTER TABLE neoscorpion_fuel_audit_entries
            ADD CONSTRAINT ck_neoscorpion_fuel_audit_entry_action
            CHECK (
                action IN (
                    'reopen_off',
                    'correct_actual',
                    'auto_hold',
                    'resume_hold',
                    'swap_fueler',
                    'swap_truck',
                    'confirm_tail',
                    'end_early'
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
    if (
        "gateway_id",
        "sort_date_operation_id",
        "door",
        "setup_needed",
        "requested_by_user_id",
    ) in unique_sets:
        return

    from app.models import NeoErmacUldRequest

    db.session.execute(text(f"ALTER TABLE {table_name} RENAME TO {legacy_table}"))
    _drop_uld_request_sqlite_indexes()
    NeoErmacUldRequest.__table__.create(
        bind=db.session.connection(),
        checkfirst=True,
    )
    _copy_uld_request_legacy_rows()
    db.session.execute(text(f"DROP TABLE {legacy_table}"))


def _restore_uld_request_sqlite_table_from_legacy(table_name, legacy_table):
    from app.models import NeoErmacUldRequest

    _drop_uld_request_sqlite_indexes()
    NeoErmacUldRequest.__table__.create(
        bind=db.session.connection(),
        checkfirst=True,
    )
    _copy_uld_request_legacy_rows()
    db.session.execute(text(f"DROP TABLE {legacy_table}"))


def _drop_uld_request_sqlite_indexes():
    db.session.execute(text("DROP INDEX IF EXISTS ix_neoermac_uld_requests_door"))
    db.session.execute(text("DROP INDEX IF EXISTS ix_neoermac_uld_requests_gateway_id"))
    db.session.execute(
        text("DROP INDEX IF EXISTS ix_neoermac_uld_requests_sort_date_operation_id")
    )
    db.session.execute(
        text("DROP INDEX IF EXISTS ix_neoermac_uld_requests_requested_by_user_id")
    )


def _copy_uld_request_legacy_rows():
    _ensure_sqlite_column(
        "neoermac_uld_requests_legacy",
        "sort_date_operation_id",
        "INTEGER",
    )
    _ensure_sqlite_column(
        "neoermac_uld_requests_legacy",
        "requested_by_user_id",
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
                requested_by_user_id,
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
                requested_by_user_id,
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
            "ALTER TABLE neoermac_uld_requests DROP CONSTRAINT IF EXISTS "
            "uq_neoermac_uld_requests_gateway_operation_door_setup"
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
                    WHERE conname = 'uq_neoermac_uld_request_scope_requester'
                ) THEN
                    ALTER TABLE neoermac_uld_requests
                    ADD CONSTRAINT uq_neoermac_uld_request_scope_requester
                    UNIQUE (
                        gateway_id,
                        sort_date_operation_id,
                        door,
                        setup_needed,
                        requested_by_user_id
                    );
                END IF;
            END
            $$;
            """
        )
    )


def _create_missing_application_tables(existing_table_names):
    from app.models import (
        AuthRateLimitState,
        FlightApiReviewItem,
        LiveScreenRefreshSetting,
        MotherBrainAlert,
        MotherBrainAlertUserState,
        MotherBrainGoogleIntegrationSetting,
        MotherBrainParkingRule,
        MotherBrainParkingSettings,
        NeoErmacDoorSupervision,
        NeoScorpionAircraftFuelSetting,
        NeoScorpionFuelAuditEntry,
        NeoScorpionFuelAssignment,
        NeoScorpionFuelingEvent,
        NeoScorpionFuelTankState,
        NeoScorpionFuelTruck,
        NeoScorpionFuelWorkState,
        NeoScorpionSettings,
        NeoScorpionSortAssetState,
        NeoScorpionSortFueler,
        NeoScorpionSortTruck,
        NeoScorpionTailFuelState,
        PortalAppAccess,
        SortDateParkingAssignment,
        SortDateAlpPreview,
        StaffingLeadershipAssignment,
        StaffingChangeRequest,
        StaffingChangeRequestEvent,
        StaffingChangeRequestItem,
        StaffingGroup,
        StaffingGroupMembership,
        StaffingNotification,
        StaffingReportingRelationship,
        StaffingDailyAttendance,
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
        AuthRateLimitState,
        FlightApiReviewItem,
        LiveScreenRefreshSetting,
        MotherBrainAlert,
        MotherBrainAlertUserState,
        MotherBrainGoogleIntegrationSetting,
        MotherBrainParkingRule,
        MotherBrainParkingSettings,
        NeoErmacDoorSupervision,
        NeoScorpionTailFuelState,
        NeoScorpionFuelTruck,
        NeoScorpionFuelAssignment,
        NeoScorpionFuelWorkState,
        NeoScorpionFuelTankState,
        NeoScorpionFuelingEvent,
        NeoScorpionFuelAuditEntry,
        NeoScorpionAircraftFuelSetting,
        NeoScorpionSettings,
        NeoScorpionSortAssetState,
        NeoScorpionSortFueler,
        NeoScorpionSortTruck,
        PortalAppAccess,
        SortDateParkingAssignment,
        SortDateAlpPreview,
        StaffingPerson,
        StaffingUnit,
        StaffingWorkAssignment,
        StaffingLeadershipAssignment,
        StaffingReportingRelationship,
        StaffingChangeRequest,
        StaffingChangeRequestItem,
        StaffingChangeRequestEvent,
        StaffingGroup,
        StaffingGroupMembership,
        StaffingNotification,
        StaffingDailyAttendance,
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


def _create_google_mission_link_table():
    from app.models import SortDateGoogleMissionLink

    SortDateGoogleMissionLink.__table__.create(
        bind=db.session.connection(),
        checkfirst=True,
    )


def _create_google_live_poll_state_table():
    from app.models import MotherBrainGoogleLivePollState

    MotherBrainGoogleLivePollState.__table__.create(
        bind=db.session.connection(),
        checkfirst=True,
    )
