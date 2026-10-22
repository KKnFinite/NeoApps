"""Narrow DELETE exception to immutable history, never an UPDATE bypass.

The exception requires a backed-up employee deletion in the SAME transaction.
Ordinary record deletions and every finalized-record update stay prohibited.
"""
from sqlalchemy import inspect, text


def install_recovery_guards(connection):
    tables = set(inspect(connection).get_table_names())
    if not {'staffing_employee_recoveries','staffing_employee_records','staffing_employee_record_events'} <= tables:
        return
    for name, condition in (('staffing_employee_records','OLD.finalized_at IS NOT NULL'),
                            ('staffing_employee_record_events','TRUE')):
        person = ('OLD.person_id' if name == 'staffing_employee_records' else
                  '(SELECT person_id FROM staffing_employee_records WHERE id=OLD.record_id)')
        if connection.dialect.name == 'postgresql':
            connection.execute(text(f"""
                CREATE OR REPLACE FUNCTION {name}_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF TG_OP = 'DELETE' AND EXISTS (
                    SELECT 1 FROM staffing_employee_recoveries r
                    WHERE r.id=current_setting('neoapps.employee_deletion', true)
                      AND r.person_id={person} AND r.state='preparing'
                      AND r.transaction_id=txid_current() AND r.encrypted_payload IS NOT NULL
                      AND r.expires_at > clock_timestamp() AT TIME ZONE 'UTC'
                  ) THEN RETURN OLD; END IF;
                  IF {condition} THEN RAISE EXCEPTION 'Employee Record history is immutable'; END IF;
                  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
                  RETURN NEW;
                END $$;
            """))
        elif connection.dialect.name == 'sqlite':
            # PREPARING is never committed. Other connections cannot use it.
            connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS {name}_immutable_delete')
            connection.exec_driver_sql(f"""
                CREATE TRIGGER {name}_immutable_delete BEFORE DELETE ON {name}
                WHEN ({condition}) AND NOT EXISTS (
                    SELECT 1 FROM staffing_employee_recoveries r WHERE r.person_id={person}
                    AND r.state='preparing' AND r.encrypted_payload IS NOT NULL
                    AND r.expires_at > strftime('%Y-%m-%d %H:%M:%f','now')
                ) BEGIN SELECT RAISE(ABORT,'Employee Record history is immutable'); END;
            """)
