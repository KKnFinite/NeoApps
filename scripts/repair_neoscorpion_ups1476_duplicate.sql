-- One explicitly approved repair, not a migration or startup task.
-- Execute only after the resume/duplicate-alias application release is LIVE.
-- No fuel quantities, physical ON timestamps, raw duplicate OFF, or inventory
-- records are rewritten. Any new evidence aborts the entire transaction.
DO $repair$
DECLARE
    a_id integer := 525;
    original_id integer := 481;
    duplicate_id integer := 482;
    actor_id integer := 1;
    a neoscorpion_fuel_assignments%ROWTYPE;
    o neoscorpion_fuel_work_states%ROWTYPE;
    d neoscorpion_fuel_work_states%ROWTYPE;
    repaired_at timestamp := timezone('UTC',clock_timestamp());
    evidence text;
    explanation text := 'Explicit user approval in Codex: administrative reconciliation of accidental OFF/new-visit duplicate; execution under maintenance Grandmaster account, not a physical Fueler action.';
BEGIN
    SELECT * INTO STRICT a FROM neoscorpion_fuel_assignments WHERE id=a_id;
    PERFORM 1 FROM sort_date_operations WHERE id=a.sort_date_operation_id FOR UPDATE;
    PERFORM 1 FROM neoscorpion_sort_asset_states WHERE sort_date_operation_id=a.sort_date_operation_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'Missing revision scope'; END IF;
    SELECT * INTO STRICT a FROM neoscorpion_fuel_assignments WHERE id=a_id FOR UPDATE;
    PERFORM 1 FROM neoscorpion_fuel_work_states WHERE id IN (original_id,duplicate_id) ORDER BY id FOR UPDATE;
    SELECT * INTO STRICT o FROM neoscorpion_fuel_work_states WHERE id=original_id;
    SELECT * INTO STRICT d FROM neoscorpion_fuel_work_states WHERE id=duplicate_id;
    PERFORM 1 FROM neoscorpion_fuel_trucks WHERE id=a.assigned_truck_id FOR UPDATE;
    IF a.active_work_state_id=original_id AND (d.performance_context->>'duplicate_of_work_id')::integer=original_id THEN RETURN; END IF;
    IF NOT EXISTS (SELECT 1 FROM users WHERE id=actor_id AND role='grandmaster' AND is_active) THEN RAISE EXCEPTION 'Authorized maintenance actor required'; END IF;
    IF a.active_work_state_id<>duplicate_id OR a.current_cycle_type<>'fuel' OR a.completed_at_utc IS NOT NULL
        OR a.fuel_on_board_at_utc IS NOT NULL OR a.transfer_fuel_gallons IS NOT NULL
        OR o.fuel_assignment_id<>a_id OR d.fuel_assignment_id<>a_id
        OR o.off_at_utc IS NULL OR d.off_at_utc IS NOT NULL OR o.manual_on_at_utc IS NULL OR d.manual_on_at_utc IS NULL
        OR d.manual_on_at_utc-o.off_at_utc NOT BETWEEN interval '0 seconds' AND interval '60 seconds'
        OR o.physical_truck_id IS DISTINCT FROM a.assigned_truck_id OR d.physical_truck_id IS DISTINCT FROM a.assigned_truck_id
        OR o.physical_fueler_user_id IS DISTINCT FROM a.assigned_fueler_user_id OR d.physical_fueler_user_id IS DISTINCT FROM a.assigned_fueler_user_id
        OR o.physical_cycle_number IS DISTINCT FROM a.current_cycle_number OR d.physical_cycle_number IS DISTINCT FROM a.current_cycle_number
        OR o.tail_number IS DISTINCT FROM d.tail_number
        OR NOT EXISTS (SELECT 1 FROM sort_date_missions m WHERE m.id=a.sort_date_mission_id AND m.assigned_tail_number=o.tail_number)
    THEN RAISE EXCEPTION 'Assignment/physical identity changed; no repair applied'; END IF;
    IF EXISTS (SELECT 1 FROM neoscorpion_fuel_work_states w WHERE w.id IN (original_id,duplicate_id) AND
        (w.fueler_submitted_at_utc IS NOT NULL OR w.physical_finalized_at_utc IS NOT NULL OR w.ended_early_at_utc IS NOT NULL
        OR w.physical_transfer_gallons IS NOT NULL OR w.physical_transfer_review OR w.continuation_review_reason IS NOT NULL))
        OR EXISTS (SELECT 1 FROM neoscorpion_fuel_tank_states t WHERE t.fuel_work_state_id IN (original_id,duplicate_id) AND t.actual_lbs IS NOT NULL)
        OR EXISTS (SELECT 1 FROM neoscorpion_fueling_events e WHERE e.fuel_assignment_id=a_id)
        OR EXISTS (SELECT 1 FROM neoscorpion_pending_uplifts p WHERE p.fuel_assignment_id=a_id AND p.status IN ('pending','review','promoted'))
    THEN RAISE EXCEPTION 'New fuel/submission/transfer/plan evidence; no repair applied'; END IF;
    IF NOT EXISTS (SELECT 1 FROM neoscorpion_fuel_audit_entries WHERE fuel_assignment_id=a_id AND field_name='physical_visit' AND old_value=original_id::text AND new_value=duplicate_id::text)
        OR EXISTS (SELECT 1 FROM neoscorpion_fuel_audit_entries WHERE fuel_assignment_id=a_id AND action IN ('swap_truck','swap_fueler') AND created_at>o.manual_on_at_utc)
    THEN RAISE EXCEPTION 'Missing duplicate link or intervening resource change'; END IF;
    IF EXISTS (SELECT 1 FROM neoscorpion_fuel_tank_states t FULL JOIN neoscorpion_fuel_tank_states s
        ON s.fuel_work_state_id=original_id AND s.tank_code=t.tank_code
        WHERE t.fuel_work_state_id=duplicate_id AND (s.id IS NULL OR t.remaining_lbs IS DISTINCT FROM s.remaining_lbs))
        OR EXISTS (SELECT 1 FROM jsonb_each(to_jsonb(d)) f WHERE f.key IN ('apu_running','apu_confirmed_at_utc','apu_allowance_lbs','automatic_apu_allowance_lbs','apu_override_allowance_lbs','applied_apu_rate_thousand_lbs_per_hour','apu_source_tank_code','notes')
            AND f.value<>'null'::jsonb AND f.value IS DISTINCT FROM to_jsonb(o)->f.key)
        OR o.apu_override_enabled IS DISTINCT FROM d.apu_override_enabled
    THEN RAISE EXCEPTION 'Conflicting or additional fuel/APU/note evidence; no repair applied'; END IF;
    IF NOT EXISTS (SELECT 1 FROM neoscorpion_sort_trucks WHERE sort_date_operation_id=a.sort_date_operation_id AND fuel_truck_id=a.assigned_truck_id AND status='available')
        OR EXISTS (SELECT 1 FROM neoscorpion_fuel_work_states w WHERE w.id NOT IN (original_id,duplicate_id)
            AND (w.physical_truck_id=a.assigned_truck_id OR w.physical_fueler_user_id=a.assigned_fueler_user_id)
            AND coalesce(w.manual_on_at_utc,w.confirmed_on_at_utc,w.on_at_utc)<=repaired_at
            AND coalesce(w.off_at_utc,w.ended_early_at_utc,w.physical_finalized_at_utc,'infinity'::timestamp)>o.off_at_utc)
    THEN RAISE EXCEPTION 'Truck unavailable or intervening physical work; no repair applied'; END IF;
    SELECT jsonb_build_object('original',to_jsonb(o),'duplicate',to_jsonb(d),'tanks',
        (SELECT jsonb_agg(to_jsonb(t) ORDER BY t.id) FROM neoscorpion_fuel_tank_states t WHERE t.fuel_work_state_id IN (original_id,duplicate_id)))::text INTO evidence;
    INSERT INTO neoscorpion_fuel_audit_entries(sort_date_operation_id,fuel_assignment_id,fuel_work_state_id,action,field_name,old_value,new_value,reason,changed_by_user_id,created_at)
        VALUES(a.sort_date_operation_id,a_id,original_id,'segment_transfer','duplicate_visit_reconciliation',evidence,jsonb_build_object('original_id',original_id,'duplicate_id',duplicate_id)::text,explanation,actor_id,repaired_at);
    INSERT INTO neoscorpion_fuel_audit_entries(sort_date_operation_id,fuel_assignment_id,fuel_work_state_id,action,field_name,old_value,new_value,reason,changed_by_user_id,created_at)
        VALUES(a.sort_date_operation_id,a_id,original_id,'reopen_off','resume_fueling','off_at_utc='||to_char(o.off_at_utc,'YYYY-MM-DD"T"HH24:MI:SS.US')||';off_by_user_id='||coalesce(o.off_by_user_id::text,''),'resumed_at_utc='||to_char(repaired_at,'YYYY-MM-DD"T"HH24:MI:SS.US'),explanation,actor_id,repaired_at);
    UPDATE neoscorpion_fuel_work_states SET performance_context=coalesce(performance_context::jsonb,'{}')||jsonb_build_object('duplicate_of_work_id',original_id),
        ended_early_at_utc=repaired_at,ended_early_by_user_id=actor_id,ended_early_reason='Administrative duplicate reconciliation; raw physical ON/OFF preserved, not a separate physical OFF. '||explanation,
        physical_finalized_at_utc=repaired_at,physical_finalized_by_user_id=actor_id,updated_at=repaired_at WHERE id=duplicate_id;
    UPDATE neoscorpion_fuel_assignments SET active_work_state_id=original_id,updated_at=repaired_at WHERE id=a_id;
    UPDATE neoscorpion_fuel_work_states SET off_at_utc=NULL,off_by_user_id=NULL,
        performance_context=coalesce(performance_context::jsonb,'{}')||jsonb_build_object('fueling_resume_version',coalesce((performance_context->>'fueling_resume_version')::integer,0)+1),updated_at=repaired_at WHERE id=original_id;
    UPDATE neoscorpion_sort_asset_states SET revision=revision+1,updated_at=repaired_at WHERE sort_date_operation_id=a.sort_date_operation_id;
END $repair$;
