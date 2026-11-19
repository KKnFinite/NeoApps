"""Global configuration eligibility, physical START locks and list controls."""
from unittest import TestCase
from app.extensions import db
from app.models import SortDateParkingAssignment, NeoSubZeroDepartureDeiceEvent, Gateway, SortDateOperation, SortDateMission
from app.services.live_collaboration import entity_version
from app.services.neosubzero_evidence import departure_event_for_tail
from tests import test_neosubzero_operations as fixtures


class GlobalOutboundTest(TestCase):
    setUp = fixtures.OperationsTest.setUp
    post = fixtures.OperationsTest.post

    def mutation(self, mission, action, **values):
        event=departure_event_for_tail(self.f.operation,mission)
        response=self.post(action=action,mission_id=mission.id,physical_tail=mission.assigned_tail_number,
            event_id=event.id if event else '',expected_version=entity_version(event),**values)
        return response

    def test_multiple_same_ramp_aircraft_configure_clear_and_reset_independently(self):
        operation = self.f.operation
        missions = [self.f.alpha, self.f.remote]
        for index, mission in enumerate(missions):
            parking = SortDateParkingAssignment.query.filter_by(sort_date_operation_id=operation.id,tail_number=mission.assigned_tail_number).first()
            parking.ramp_code='A';parking.position_code=f'A0{index+1}'
        db.session.commit()
        for mission in missions:
            response=self.post(action='initial_contact',mission_id=mission.id,physical_tail=mission.assigned_tail_number,expected_version='')
            self.assertEqual(response.status_code,200,response.json)
            event=departure_event_for_tail(operation,mission)
            response=self.post(action='toggle_configured',mission_id=mission.id,physical_tail=mission.assigned_tail_number,
                event_id=event.id,expected_version=entity_version(event))
            self.assertEqual(response.status_code,200,response.json)
            self.assertEqual(event.status,'configured')
        first,second=[departure_event_for_tail(operation,m) for m in missions]
        response=self.post(action='toggle_configured',event_id=first.id,expected_version=entity_version(first))
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(second.status,'configured')
        response=self.post(action='reset',event_id=first.id,expected_version=entity_version(first))
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(second.status,'configured')
        self.assertEqual(NeoSubZeroDepartureDeiceEvent.query.count(),2)

    def test_configuration_is_not_hardcoded_to_gateway_sort_or_ramp(self):
        from datetime import date,datetime
        from app.services.neosubzero_departure_deice import mutate_departure_deice
        for code,sort,ramp in [('SYN-A','day','B'),('SYN-B','twilight','E')]:
            gateway=Gateway(code=code,name=code,is_active=True);db.session.add(gateway);db.session.flush()
            operation=SortDateOperation(gateway_id=gateway.id,gateway_code=code,sort_date=date(2026,11,18),sort_name=sort)
            db.session.add(operation);db.session.flush()
            for index in range(2):
                tail=f'{code}-{index}'
                mission=SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,gateway_code=code,sort_name=sort,
                    mission_type='departure',mission_source='manual',flight_number=f'FIX-{code}-{index}',assigned_tail_number=tail,origin=code,destination='SYN',
                    timezone='America/Chicago',planned_datetime_utc=datetime(2026,11,18,20))
                db.session.add(mission);db.session.flush()
                db.session.add(SortDateParkingAssignment(sort_date_operation_id=operation.id,tail_number=tail,ramp_code=ramp,position_code=f'{ramp}0{index+1}'))
                event=mutate_departure_deice(operation,mission,'initial_contact',{},actor_id=self.user.id)
                mutate_departure_deice(operation,mission,'toggle_configured',{},event=event,actor_id=self.user.id)
                self.assertEqual(event.status,'configured')
            db.session.commit()

    def test_shared_ramp_configuration_does_not_bypass_pad_or_radio_start_lock(self):
        first,second=self.f.alpha,self.f.remote
        parking=SortDateParkingAssignment.query.filter_by(sort_date_operation_id=self.f.operation.id,tail_number=second.assigned_tail_number).one()
        parking.ramp_code='A';parking.position_code='A02';db.session.commit()
        for mission in (first,second):
            self.assertEqual(self.mutation(mission,'initial_contact').status_code,200)
            self.assertEqual(self.mutation(mission,'toggle_configured').status_code,200)
        fields=dict(treatment_plan='one_type_i',pass1_surface_area='wings_only',pass1_start='22:10')
        response=self.mutation(first,'save_treatment',**fields)
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['version'],entity_version(departure_event_for_tail(self.f.operation,first)))
        self.assertEqual(self.mutation(second,'save_treatment',**fields).status_code,409)
        self.assertEqual(self.mutation(first,'save_treatment',**fields,pass1_end='22:15').status_code,200)
        self.assertEqual(self.mutation(first,'clear').status_code,200)
        self.assertEqual(self.mutation(second,'save_treatment',**fields).status_code,409)
        self.assertEqual(self.mutation(first,'radio_complete').status_code,200)
        self.assertEqual(self.mutation(second,'save_treatment',**fields).status_code,200)

    def test_all_plans_time_entry_and_acknowledged_versions_keep_stale_guard(self):
        from app.services.neosubzero_operations import operations_context
        for plan,mission in zip(('one_type_i','two_type_i','type_i_type_iv'),(self.f.alpha,self.f.remote,self.f.bravo)):
            self.assertEqual(self.mutation(mission,'initial_contact').status_code,200)
            event=departure_event_for_tail(self.f.operation,mission)
            old=entity_version(event)
            values=dict(treatment_plan=plan,pass1_surface_area='wings_only',pass1_start='22:10',pass1_end='22:15')
            if plan!='one_type_i':values.update(pass2_surface_area='wings_tail',pass2_start='22:16',pass2_end='22:20')
            response=self.mutation(mission,'save_treatment',**values)
            self.assertEqual(response.status_code,200,response.json)
            self.assertEqual(response.json['version'],entity_version(event))
            self.assertEqual(event.status,'finished')
            row=next(r for r in operations_context(self.f.gateway,self.f.operation,'outbound')['rows'] if r['mission_id']==mission.id)
            self.assertEqual(row['final_start'],'22:10' if plan=='one_type_i' else '22:16')
            self.assertEqual(self.post(action='toggle_configured',mission_id=mission.id,physical_tail=mission.assigned_tail_number,
                event_id=event.id,expected_version=old).status_code,409)
            self.assertEqual(self.mutation(mission,'clear').status_code,200)
            self.assertEqual(self.mutation(mission,'radio_complete').status_code,200)

    def test_departure_keeps_finished_clear_and_manual_radio_available(self):
        from app.services.neosubzero_operations import operations_context

        mission = self.f.alpha
        self.assertEqual(self.mutation(mission, 'initial_contact').status_code, 200)
        times = dict(treatment_plan='one_type_i', pass1_surface_area='wings_tail',
                     pass1_start='22:10', pass1_end='22:15')
        self.assertEqual(self.mutation(mission, 'save_treatment', **times).status_code, 200)
        mission.departure_status = 'departed'
        db.session.commit()

        def read_row():
            context = operations_context(self.f.gateway, self.f.operation, 'outbound')
            return next(row for row in context['rows'] if row['mission_id'] == mission.id)

        finished = read_row()
        self.assertEqual(finished['visual_state'], 'fin')
        self.assertFalse(finished['mobile_open'])
        self.assertTrue(finished['desktop_clear'])
        self.assertEqual(finished['quick_actions'], (('clear', 'CLEAR'),))
        self.assertEqual(self.mutation(mission, 'clear').status_code, 200)
        pending = read_row()
        self.assertTrue(pending['radio_pending'])
        self.assertIsNone(pending['event'].radio_completed_at_utc)
        self.assertEqual(self.mutation(mission, 'radio_complete').status_code, 200)
        self.assertTrue(read_row()['radio_complete'])
        self.assertEqual(read_row()['pass1_end'], '22:15')
