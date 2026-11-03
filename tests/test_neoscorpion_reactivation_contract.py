"""R03G authorization, atomic follow-up and read-only visit evidence contracts."""
import copy
import re
import unittest
from datetime import timedelta
from unittest.mock import patch
from sqlalchemy import event as sql_event
from app.extensions import db
from app.models import (NeoScorpionFuelAssignment,NeoScorpionFuelWorkState,NeoScorpionFuelTankState,
    NeoScorpionFuelingEvent,NeoScorpionSortTruck,NeoScorpionFuelAuditEntry,NeoScorpionTruckInventoryAudit,
    NeoScorpionSortAssetState,NeoScorpionFuelCycleHistory,NeoScorpionTailFuelState,SortDateOperation,SortDateMission)
from app.services import neoscorpion as fuel
from app.services.neoscorpion_drivers import change_driver,driver_context
from tests import test_neoscorpion_dispatch_order as cases
from tests.neoscorpion_reactivation_workflow import assign_resources,event_snapshot
from tests.neoscorpion_handoff_fixture import submit_fueler_only


def snapshot():
    db.session.expire_all()
    models=(NeoScorpionFuelAssignment,NeoScorpionFuelWorkState,NeoScorpionFuelTankState,NeoScorpionFuelingEvent,
        NeoScorpionSortTruck,NeoScorpionFuelAuditEntry,NeoScorpionTruckInventoryAudit,NeoScorpionSortAssetState,
        NeoScorpionFuelCycleHistory,NeoScorpionTailFuelState,SortDateMission)
    return copy.deepcopy({model.__tablename__:[{c.name:getattr(row,c.name) for c in model.__table__.columns}
        for row in model.query.order_by(model.id)] for model in models})


class ReactivationContractTest(unittest.TestCase):
    def setUp(self):
        self.f=cases.DispatchReactivationTest();self.f.setUp();self.addCleanup(self.f.doCleanups);self.f.prepare()

    def closed_parent(self):
        f=self.f;f.save(f.start+timedelta(minutes=10),**f.final_data());f.off()
        submit_fueler_only(f.gateway,f.assignment.id,now_utc=f.start+timedelta(minutes=13));db.session.commit()
        fuel.complete_fueled_assignment(f.gateway,f.dispatcher,f.assignment.id,now_utc=f.start+timedelta(minutes=14));db.session.commit()
        return NeoScorpionFuelingEvent.query.one()

    def followup_data(self,**values):
        return dict(assignment_id=self.f.assignment.id,cycle_type='uplift',required_fuel='35',expected_cycle='1',expected_tail='N491UP',**values)

    def post(self,user,path,data):
        email=user.email
        with self.f.fixture.app.app_context():
            client=self.f.fixture.app.test_client()
            credentials={'email':email,'password':'TestPassword123!'}
            if self.f.fixture.app.config.get('CSRF_PROTECT_TESTING'):
                token=re.search(rb'name="csrf_token" value="([^"]+)"',client.get('/login').data)
                self.assertIsNotNone(token);credentials['csrf_token']=token.group(1).decode()
            self.assertEqual(client.post('/login',data=credentials).status_code,302)
            return client.post(path,data=data)

    def test_followup_permission_stale_cycle_sort_and_csrf_preserve_closed_parent(self):
        f=self.f;event=self.closed_parent();original=event_snapshot(event)
        operation=SortDateOperation(gateway_id=f.gateway.id,gateway_code=f.gateway.code,sort_name='night',sort_date=f.operation.sort_date-timedelta(days=1))
        db.session.add(operation);db.session.flush()
        foreign=SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,gateway_code=f.gateway.code,
            sort_name='night',mission_type='departure',mission_source='manual',flight_number='UPS-FOREIGN',assigned_tail_number='N491UP',
            origin=f.gateway.code,destination='SDF',timezone='America/Chicago')
        db.session.add(foreign);db.session.flush()
        assignment=NeoScorpionFuelAssignment(sort_date_operation_id=operation.id,sort_date_mission_id=foreign.id,confirmed_tail_number='N491UP')
        db.session.add(assignment);db.session.commit();before=snapshot()
        path='/neoscorpion/fuel-dispatch/start-follow-up';data=self.followup_data()
        self.assertEqual(self.post(f.fueler,path,data).status_code,403);self.assertEqual(snapshot(),before)
        for values in ({'expected_cycle':'0'},{'expected_tail':'OTHER'},{'assignment_id':assignment.id}):
            with self.subTest(values=values):
                self.assertEqual(self.post(f.dispatcher,path,{**data,**values}).status_code,400)
                self.assertEqual(snapshot(),before)
        with patch.dict(f.fixture.app.config,{'CSRF_PROTECT_TESTING':True}):
            self.assertEqual(self.post(f.dispatcher,path,data).status_code,400)
        self.assertEqual(snapshot(),before)
        self.assertEqual(self.post(f.dispatcher,path,data).status_code,302)
        self.assertEqual(self.post(f.dispatcher,path,data).status_code,400)
        db.session.refresh(f.assignment)
        self.assertEqual(f.assignment.current_cycle_number,2)
        self.assertEqual(NeoScorpionFuelWorkState.query.filter_by(fuel_assignment_id=f.assignment.id).count(),2)
        self.assertEqual((NeoScorpionFuelingEvent.query.count(),f.selection.current_gallons),(1,7000))
        self.assertEqual(event_snapshot(event),original)

    def test_partial_followup_failure_rolls_back_then_retry_allocates_one_cycle(self):
        f=self.f;self.closed_parent();before=snapshot();allocate=fuel.new_work
        def fail(*args,**kwargs):
            allocate(*args,**kwargs);db.session.flush();raise RuntimeError('synthetic allocation failure')
        with patch.object(fuel,'new_work',side_effect=fail),self.assertRaisesRegex(RuntimeError,'synthetic allocation'):
            fuel.start_follow_up_fuel_cycle(f.gateway,f.dispatcher,f.assignment.id,'uplift','35',None,None,expected_cycle=1,expected_tail='N491UP')
        db.session.rollback();self.assertEqual(snapshot(),before)
        fuel.start_follow_up_fuel_cycle(f.gateway,f.dispatcher,f.assignment.id,'uplift','35',None,None,expected_cycle=1,expected_tail='N491UP');db.session.commit()
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),2)
        self.assertEqual((NeoScorpionFuelingEvent.query.count(),f.selection.current_gallons),(1,7000))

    def test_driver_read_is_operationally_read_only_and_separates_closed_and_upcoming_visits(self):
        f=self.f;driver=f.fixture._login_user('r03g-driver','operator')
        change_driver(f.gateway,f.operation,driver,f.truck.id,driver.id,expected_driver_id=None,expected_driver_truck_id=None);db.session.commit()
        event=self.closed_parent();original=event_snapshot(event)
        fuel.start_follow_up_fuel_cycle(f.gateway,f.dispatcher,f.assignment.id,'uplift','35',None,None,expected_cycle=1,expected_tail='N491UP');db.session.commit()
        assign_resources(f,f.assignment,f.truck,f.fueler)
        fresh=db.session.get(NeoScorpionFuelWorkState,f.assignment.active_work_state_id)
        before=snapshot();writes=[]
        def record(_conn,_cursor,statement,*_args):
            if statement.lstrip().split(' ',1)[0].upper() in ('INSERT','UPDATE','DELETE') and 'neoscorpion_' in statement.lower():writes.append(statement)
        sql_event.listen(db.engine,'before_cursor_execute',record)
        try:
            context=driver_context(f.gateway,driver);db.session.flush()
            for path in ('/neoscorpion/truck-driver','/neoscorpion/truck-driver/revision','/neoscorpion/truck-driver/live-panel'):
                email=driver.email
                with f.fixture.app.app_context():
                    client=f.fixture.app.test_client();client.post('/login',data={'email':email,'password':'TestPassword123!'})
                    self.assertEqual(client.get(path).status_code,200)
        finally:sql_event.remove(db.engine,'before_cursor_execute',record)
        self.assertFalse(writes);self.assertEqual(snapshot(),before)
        self.assertEqual(context['driver_groups']['current'],[])
        previous=next(item['row'] for item in context['driver_groups']['previous'] if item['row']['physical_work_id']==f.work.id)
        upcoming=next(item['row'] for item in context['driver_groups']['upcoming'] if item['row']['physical_work_id']==fresh.id)
        self.assertEqual([t['actual_lbs'] for t in previous['tank_rows']],[15000,0,15000])
        self.assertTrue(all(t['actual_lbs'] is None for t in upcoming['tank_rows']))
        self.assertEqual([t['remaining_lbs'] for t in upcoming['tank_rows']],[15000,0,15000])
        self.assertNotEqual(previous['physical_work_id'],upcoming['physical_work_id'])
        self.assertEqual(event_snapshot(event),original)
