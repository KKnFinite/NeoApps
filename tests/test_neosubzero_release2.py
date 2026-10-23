"""Release 2 physical-tail ownership and participation regressions."""
import unittest
from unittest.mock import patch
from decimal import Decimal
from flask import g
from datetime import datetime

from app.extensions import db
from app.models import NeoSubZeroSprayRecord, NeoSubZeroUccTruckAssignment, SortDateParkingAssignment
from tests import test_neosubzero_ucc as fixtures
from app.services.neosubzero_departure_deice import departure_deice_context, mutate_departure_deice
from app.services.neosubzero_evidence import NeoSubZeroEvidenceError,departure_event_for_tail,application_context,save_application_context
from app.services.neosubzero_participation import change_participation,participation_query
from app.services.neosubzero_departure_deice import neosubzero_fluid_settings
from app.services.live_collaboration import entity_version
from app.services.neosubzero_spray import set_neosubzero_spray_gallons,neosubzero_deice_log
from app.services.neosubzero_ucc import set_neosubzero_ucc_assignment,ucc_staffing_version
from app.models import NeoSubZeroDepartureDeiceEvent,NeoSubZeroUccAssignment,NeoSubZeroAuditEntry,SortDateMission,SortDateOperation,StaffingPerson


class Release2Test(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.NeoSubZeroUccTest();self.f.setUp();self.addCleanup(self.f.tearDown)

    def test_departure_follows_physical_tail_after_swap(self):
        f=self.f;event=f._deice_event(f.alpha,'configured');physical_tail=event.tail_number
        second=f._departure('5X777', 'N777', 'DEN', 4)
        f.alpha.assigned_tail_number='N777';second.assigned_tail_number=physical_tail;db.session.commit()
        rows={r['mission_id']:r for r in departure_deice_context(f.gateway,f.operation)['rows']}
        self.assertIsNone(rows[f.alpha.id]['event'])
        self.assertEqual(rows[second.id]['event'].id,event.id)
        self.assertEqual(event.sort_date_mission_id,f.alpha.id)

    def test_unsprayed_clear_has_no_fake_spray_or_fin(self):
        f=self.f
        event=mutate_departure_deice(f.operation,f.alpha,'clear',{})
        db.session.commit()
        self.assertEqual(event.status,'cleared')
        self.assertIsNone(event.pass1_started_at_utc)
        self.assertIsNone(event.pass1_ended_at_utc)
        self.assertEqual(NeoSubZeroSprayRecord.query.count(),0)

    def test_end_snapshots_participation_without_gallons(self):
        f=self.f
        db.session.add(NeoSubZeroUccTruckAssignment(sort_date_operation_id=f.operation.id,ramp='Alpha',position_number=1,truck_number='T12'))
        event=mutate_departure_deice(f.operation,f.alpha,'initial_contact',{})
        mutate_departure_deice(f.operation,f.alpha,'save_treatment',dict(treatment_plan='one_type_i',pass1_surface_area='wings_only',pass1_start='2200',pass1_end='2205'),event=event)
        db.session.commit()
        record=NeoSubZeroSprayRecord.query.one()
        self.assertEqual(record.truck_number_snapshot,'T12')
        self.assertIsNone(record.gallons)
        self.assertEqual(record.started_at_utc,event.pass1_started_at_utc)
        self.assertEqual(record.ended_at_utc,event.pass1_ended_at_utc)

    def flow(self,plan='one_type_i',end='2205'):
        f=self.f
        set_neosubzero_ucc_assignment(f.operation,'Alpha',1,'driver',f.permanent)
        db.session.add(NeoSubZeroUccTruckAssignment(sort_date_operation_id=f.operation.id,ramp='Alpha',position_number=1,truck_number='T12'))
        event=mutate_departure_deice(f.operation,f.alpha,'initial_contact',{})
        values=dict(treatment_plan=plan,pass1_surface_area='wings_only',pass1_start='2200',pass1_end=end,pass2_surface_area='wings_tail')
        mutate_departure_deice(f.operation,f.alpha,'save_treatment',values,event=event)
        db.session.commit();return event,values

    def client(self,role='simulator',person=None):
        f=self.f;client=f.app.test_client();user=f._user('r2-'+role+str(getattr(person,'id','')),role)
        if person:user.employee_id=person.employee_id;db.session.commit()
        g.pop('_login_user',None)
        f._login(client,user);return client

    def post(self,client,path,**values):
        f=self.f
        data={'operation_id':f.operation.id,'mission_id':f.alpha.id,'physical_tail':f.alpha.assigned_tail_number,'board':'coordinator',**values}
        g.pop('_login_user',None)
        with patch('app.neonodes.neosubzero.routes.current_neosubzero_operation',return_value=f.operation):
            return client.post('/neosubzero/'+path,data=data,headers={'X-Requested-With':'XMLHttpRequest'})

    def test_independent_pass_snapshots_and_shared_team_changes(self):
        f=self.f;event,values=self.flow('type_i_type_iv')
        first=participation_query(event).filter_by(pass_number=1).one()
        self.assertEqual(first.driver_person_id,f.permanent.id)
        client=self.client()
        result=self.post(client,'team',action='change_truck',ramp='Alpha',position_number=1,truck_number='T18',staffing_version=ucc_staffing_version(f.operation))
        self.assertEqual(result.status_code,200,result.json)
        token=ucc_staffing_version(f.operation)
        result=self.post(client,'team',action='change_deicers',ramp='Alpha',position_number=1,driver_person_id=f.callout.id,flyer_person_id='',staffing_version=token)
        self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(self.post(client,'team',action='change_truck',ramp='Alpha',position_number=1,truck_number='T19',staffing_version=token).status_code,409)
        values.update(pass2_start='2206',pass2_end='2208')
        mutate_departure_deice(f.operation,f.alpha,'save_treatment',values,event=event);db.session.commit()
        records=participation_query(event).order_by(NeoSubZeroSprayRecord.pass_number).all()
        self.assertEqual([(r.truck_number_snapshot,r.driver_person_id) for r in records],[('T12',f.permanent.id),('T18',f.callout.id)])
        self.assertEqual(records[1].concentration_percent_snapshot,100)
        self.assertEqual(event.status,'finished')
        mutate_departure_deice(f.operation,f.alpha,'clear',{},event=event);db.session.commit()
        self.assertEqual(event.status,'cleared')
        self.assertTrue(all(r.gallons is None for r in records))

    def test_add_remove_max_four_duplicates_and_applied_gallons_retained(self):
        f=self.f;event,values=self.flow(end='')
        settings=neosubzero_fluid_settings(f.gateway)
        for number in (18,19,20):
            change_participation(f.operation,f.alpha,event,'departure','add_truck',dict(pass_number='1',truck_number='T'+str(number)),settings,actor_id=None)
        db.session.commit()
        self.assertEqual(participation_query(event).count(),4)
        with self.assertRaises(NeoSubZeroEvidenceError):
            change_participation(f.operation,f.alpha,event,'departure','add_truck',dict(pass_number='1',truck_number='T21'),settings,actor_id=None)
        with self.assertRaises(NeoSubZeroEvidenceError):
            change_participation(f.operation,f.alpha,event,'departure','add_truck',dict(pass_number='1',truck_number='T18'),settings,actor_id=None)
        first=participation_query(event).filter_by(position_number=1).one()
        self.assertEqual(first.truck_number_snapshot,'T12')
        values['pass1_end']='2205';mutate_departure_deice(f.operation,f.alpha,'save_treatment',values,event=event);db.session.commit()
        set_neosubzero_spray_gallons(f.operation,f.alpha,event,1,1,'12.5',record=first);db.session.commit()
        change_participation(f.operation,f.alpha,event,'departure','remove_truck',dict(pass_number='1',record_id=first.id,record_version=entity_version(first),correction_reason='Removed from future work; applied gallons remain'),settings,actor_id=None)
        db.session.commit()
        self.assertEqual(first.gallons,Decimal('12.5'));self.assertFalse(first.participation_selected);self.assertIsNone(first.excluded_at_utc)
        self.assertEqual(neosubzero_deice_log(f.operation)[0]['applications'][0]['gallons'],'12.5')
        with self.assertRaises(ValueError):set_neosubzero_spray_gallons(f.operation,f.alpha,event,1,1,'',record=first)

    def test_clear_all_outcomes_and_newest_cleared_last_group(self):
        f=self.f
        for action,outcome in ((None,'no_deice'),('set_negative','negative'),('manual_not_sprayed','not_sprayed')):
            mission=f._departure('5X-CLEAR-'+outcome,'N-'+outcome,'DEN',4)
            event=mutate_departure_deice(f.operation,mission,action,{}) if action else None
            event=mutate_departure_deice(f.operation,mission,'clear',{},event=event);db.session.commit()
            self.assertEqual(event.clear_outcome,outcome);self.assertIsNone(event.pass1_started_at_utc)
        from app.services.neosubzero_operations import operations_context
        rows=operations_context(f.gateway,f.operation,'outbound')['rows']
        self.assertEqual([r['visual_state'] for r in rows][-3:],['cleared']*3)
        self.assertEqual([r['flight'] for r in rows][-3:],['5X-CLEAR-not_sprayed','5X-CLEAR-negative','5X-CLEAR-no_deice'])
        self.assertEqual(NeoSubZeroSprayRecord.query.count(),0)

    def test_post_clear_corrections_keep_clear_clock_and_immutable_before_after(self):
        f=self.f;event,_values=self.flow()
        mutate_departure_deice(f.operation,f.alpha,'clear',{},event=event);db.session.commit()
        original_clear=event.cleared_at_utc
        record=participation_query(event).one();original_name=record.driver_name_snapshot
        client=self.client()
        response=self.post(client,'participation',action='correct_participant',pass_number=1,record_id=record.id,record_version=entity_version(record),expected_version=entity_version(event),truck_number='T18',crew_confirmed='yes',driver_person_id=f.callout.id,correction_reason='Actual relief team correction')
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(event.status,'cleared');self.assertEqual(event.cleared_at_utc,original_clear)
        audit=NeoSubZeroAuditEntry.query.filter_by(action='correct_participant').one()
        self.assertEqual(audit.before_values['driver_name_snapshot'],original_name)
        self.assertEqual(audit.after_values['driver_person_id'],f.callout.id)
        self.assertEqual(record.truck_number_snapshot,'T18')
        self.assertIsNone(record.gallons)

    def test_driver_keeps_own_gallons_after_reassignment_cannot_change_staff(self):
        f=self.f;event,_=self.flow();record=participation_query(event).one()
        client=self.client('operator',f.permanent)
        assignment=NeoSubZeroUccAssignment.query.filter_by(person_id=f.permanent.id).one()
        assignment.ramp='Bravo';db.session.commit()
        with patch('app.neonodes.neosubzero.routes.current_neosubzero_operation',return_value=f.operation):
            page=client.get('/neosubzero/deicer-mobile');self.assertEqual(page.status_code,200)
            self.assertIn(b'data-own-participant',page.data);self.assertNotIn(b'SAVE TRUCK',page.data)
        response=self.post(client,'spray-gallons',board='deicer',record_id=record.id,expected_version=entity_version(record),gallons='14')
        self.assertEqual(response.status_code,200,response.json);self.assertEqual(record.gallons,14)
        other=self.client('operator',f.callout)
        self.assertEqual(self.post(other,'spray-gallons',board='deicer',record_id=record.id,expected_version=entity_version(record),gallons='50').status_code,403)
        self.assertEqual(self.post(client,'truck',board='deicer',ramp='Bravo',position_number=1,truck_number='OTHER').status_code,403)
        self.assertEqual(self.post(client,'team',board='deicer',action='change_truck').status_code,403)
        self.assertEqual(self.post(client,'participation',board='deicer',action='add_truck').status_code,403)

    def test_stale_participation_double_click_and_tail_forms_reject(self):
        f=self.f;event,_=self.flow(end='');client=self.client();version=entity_version(event)
        values=dict(action='add_truck',pass_number=1,expected_version=version,truck_number='T18')
        self.assertEqual(self.post(client,'participation',**values).status_code,200)
        self.assertEqual(self.post(client,'participation',**values).status_code,409)
        self.assertEqual(participation_query(event).count(),2)
        f.alpha.assigned_tail_number='NEWTAIL';db.session.commit()
        self.assertEqual(self.post(client,'participation',physical_tail=event.tail_number,**values).status_code,409)

    def test_shared_context_survives_sessions_stale_rejected(self):
        f=self.f;client=self.client()
        response=self.post(client,'operations/mutate',mode='outbound',target='/neosubzero/application-context',reason_for_application='Ice',active_precipitation='Snow')
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(application_context(f.operation)['active_precipitation'],'Snow')
        response=self.post(client,'operations/mutate',mode='outbound',target='/neosubzero/application-context',reason_for_application='Frost',active_precipitation='Rain')
        self.assertEqual(response.status_code,409,response.json)
        self.assertEqual(application_context(f.operation)['reason_for_application'],'Ice')

    def test_tail_swap_during_and_after_actual_spray_keeps_provenance(self):
        f=self.f;event,values=self.flow(end='')
        old=f.alpha.assigned_tail_number;original_mid=f.alpha.id
        other=f._departure('5X-SWAP','REPLACEMENT','DEN',4)
        f.alpha.assigned_tail_number='REPLACEMENT';other.assigned_tail_number=old;db.session.commit()
        self.assertEqual(departure_event_for_tail(f.operation,other).id,event.id)
        self.assertIsNone(departure_event_for_tail(f.operation,f.alpha))
        values['pass1_end']='2205';mutate_departure_deice(f.operation,other,'save_treatment',values,event=event);db.session.commit()
        record=participation_query(event).one()
        self.assertEqual(event.sort_date_mission_id,original_mid)
        self.assertEqual(event.flight_number_snapshot,'5X200')
        self.assertEqual(record.tail_number_snapshot,old)
        # Release 3 freezes original pass context at START, not the END-time flight.
        self.assertEqual(record.sort_date_mission_id,original_mid)
        self.assertEqual(record.flight_number_snapshot,'5X200')
        mutate_departure_deice(f.operation,other,'clear',{},event=event);db.session.commit()
        f.alpha.assigned_tail_number=old;other.assigned_tail_number='REPLACEMENT';db.session.commit()
        self.assertEqual(departure_event_for_tail(f.operation,f.alpha).status,'cleared')
        self.assertIsNone(departure_event_for_tail(f.operation,other))
        self.assertEqual(neosubzero_deice_log(f.operation)[0]['tail'],old)

    def test_pretreat_tail_ownership_snapshots_and_independent_departure(self):
        from app.neonodes.neosubzero.services import mutate_pretreat,pretreat_context
        f=self.f
        set_neosubzero_ucc_assignment(f.operation,'Alpha',1,'driver',f.permanent)
        db.session.add(NeoSubZeroUccTruckAssignment(sort_date_operation_id=f.operation.id,ramp='Alpha',position_number=1,truck_number='PT12'))
        values=dict(pass1_surface_area='wings_only',pass2_surface_area='entire_aircraft',pass1_start='2100',pass1_end='2102',pass2_start='2103',pass2_end='2106')
        state=mutate_pretreat(f.operation,f.alpha,'save_treatment',values);db.session.commit()
        self.assertEqual(participation_query(state,'pretreat').count(),2)
        self.assertTrue(all(r.departure_deice_event_id is None and r.gallons is None for r in participation_query(state,'pretreat')))
        old=f.alpha.assigned_tail_number;original_mid=f.alpha.id
        other=f._departure('5X-PTSWAP','OTHER','DEN',4)
        f.alpha.assigned_tail_number='OTHER';other.assigned_tail_number=old;db.session.commit()
        rows={r['mission_id']:r for r in pretreat_context(f.gateway,f.operation)['rows']}
        self.assertIsNone(rows[f.alpha.id]['state']);self.assertEqual(rows[other.id]['state'].id,state.id)
        self.assertEqual(state.original_mission_id,original_mid)
        event=mutate_departure_deice(f.operation,other,'clear',{});db.session.commit()
        self.assertEqual(event.clear_outcome,'pretreated')
        self.assertTrue(all(r.pretreat_state_id==state.id for r in NeoSubZeroSprayRecord.query.all()))
        self.assertEqual({g['kind'] for g in neosubzero_deice_log(f.operation)},{'pretreat'})

    def test_completed_product_and_weather_do_not_follow_current_settings(self):
        from app.services.neosubzero_evidence import save_application_context
        f=self.f
        save_application_context(f.operation,dict(active_precipitation='Snow',ambient_temperature='20 F',dew_point='18 F'),actor_id=None)
        db.session.commit()
        event,_=self.flow();record=participation_query(event).one()
        original=(record.fluid_name_snapshot,record.concentration_percent_snapshot,record.active_precipitation)
        from app.services.neosubzero_departure_deice import set_neosubzero_fluid_settings
        set_neosubzero_fluid_settings(f.gateway,'Different future product',70,'Future Type IV')
        save_application_context(f.operation,dict(expected_version=application_context(f.operation)['version'],active_precipitation='Rain'),actor_id=None)
        mutate_departure_deice(f.operation,f.alpha,'clear',{},event=event);db.session.commit()
        row=next(r for r in departure_deice_context(f.gateway,f.operation)['rows'] if r['mission_id']==f.alpha.id)
        self.assertEqual(row['final_fluid']['name'],original[0]);self.assertEqual(row['script']['concentration'],original[1])
        self.assertEqual(record.active_precipitation,'Snow')

    def test_ambiguous_legacy_active_events_require_review_not_merge(self):
        from app.services.neosubzero_operations import operations_context
        f=self.f;first=f._deice_event(f.alpha,'deice_planned')
        second=f._departure('5X-AMBIG',f.alpha.assigned_tail_number,'DEN',4)
        duplicate=f._deice_event(second,'configured');db.session.commit()
        with self.assertRaises(NeoSubZeroEvidenceError):departure_event_for_tail(f.operation,f.alpha)
        row=next(r for r in operations_context(f.gateway,f.operation,'outbound')['rows'] if r['mission_id']==f.alpha.id)
        self.assertEqual(row['visual_state'],'review');self.assertTrue(row['ownership_review'])
        self.assertEqual(NeoSubZeroDepartureDeiceEvent.query.count(),2)
        self.assertIsNone(first.pass1_started_at_utc);self.assertIsNone(duplicate.pass1_started_at_utc)

    def test_sort_rollover_rejects_old_participation_without_mutation(self):
        from datetime import timedelta
        f=self.f;event,_=self.flow(end='');client=self.client();old=f.operation.id
        future=SortDateOperation(sort_date=f.operation.sort_date+timedelta(days=1),gateway_id=f.gateway.id,gateway_code=f.gateway.code,sort_name=f.operation.sort_name)
        db.session.add(future);db.session.commit()
        with patch('app.neonodes.neosubzero.routes.current_neosubzero_operation',return_value=future):
            response=client.post('/neosubzero/participation',data=dict(operation_id=old,mission_id=f.alpha.id,physical_tail=f.alpha.assigned_tail_number,action='add_truck',pass_number=1,truck_number='ROLLOVER',expected_version=entity_version(event)),headers={'X-Requested-With':'XMLHttpRequest'})
            self.assertEqual(response.status_code,409,response.json)
            self.assertEqual(client.get('/neosubzero/operations').status_code,200)
        self.assertEqual(NeoSubZeroSprayRecord.query.count(),0)

    def test_pass2_plan_change_before_start_updates_only_its_official_fluid_type(self):
        f=self.f;event,values=self.flow('two_type_i')
        settings=neosubzero_fluid_settings(f.gateway)
        change_participation(f.operation,f.alpha,event,'departure','add_truck',dict(pass_number=2,truck_number='PRESELECTED18'),settings,actor_id=None)
        db.session.commit()
        self.assertEqual(participation_query(event).filter_by(pass_number=2,truck_number_snapshot='PRESELECTED18').one().pass_type,'type_i')
        values.update(treatment_plan='type_i_type_iv',pass2_start='2206',pass2_end='2210')
        mutate_departure_deice(f.operation,f.alpha,'save_treatment',values,event=event);db.session.commit()
        self.assertEqual({r.pass_type for r in participation_query(event).filter_by(pass_number=1)},{'type_i'})
        self.assertEqual({r.pass_type for r in participation_query(event).filter_by(pass_number=2)},{'type_iv'})
        self.assertTrue(all(r.concentration_percent_snapshot==100 for r in participation_query(event).filter_by(pass_number=2)))
