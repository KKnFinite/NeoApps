import unittest
from datetime import timedelta
from unittest.mock import patch
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from app.extensions import db
from app.models import NeoScorpionTruckDriver, NeoScorpionTruckDriverAudit, NeoScorpionFuelingEvent, User
from flask import render_template
from app.services import neoscorpion as service
from app.services.neoscorpion_drivers import change_driver, driver_context
from app.services.schema_sync import _create_missing_application_tables
from tests import test_neoscorpion_operational_timeline as timeline


class TruckDriverTest(unittest.TestCase):
    def prepare(self, lead=15):
        if not hasattr(self,'work'):
            timeline.OperationalTimelineTest.prepare(self,lead)
    save=timeline.OperationalTimelineTest.save
    off=timeline.OperationalTimelineTest.off
    final_data=timeline.OperationalTimelineTest.final_data
    login=timeline.OperationalTimelineTest.login
    _exercise_returning_tail=timeline.OperationalTimelineTest._exercise_returning_tail

    def setUp(self):
        timeline.OperationalTimelineTest.setUp(self)
        self.prepare()
        self.driver=self.fixture._login_user("truck-driver-one","operator")
        self.other=self.fixture._login_user("truck-driver-two","operator")
        db.session.commit()

    def claim(self, user=None, truck=None, expected=None, own=None, **kw):
        result=change_driver(self.gateway,self.operation,user or self.driver,(truck or self.truck).id,
            (user or self.driver).id,expected_driver_id=expected,expected_driver_truck_id=own,**kw)
        db.session.commit()
        return result

    def test_selection_persistence_switch_and_idempotent_audit(self):
        self.claim()
        self.login(self.driver)
        self.assertIn(b"TRUCK 20",self.client.get('/neoscorpion/truck-driver').data)
        self.client.post('/logout')
        second_client=self.fixture.app.test_client()
        second_client.post('/login',data={'email':self.driver.email,'password':'TestPassword123!'})
        self.assertIn(b"TRUCK 20",second_client.get('/neoscorpion/truck-driver').data)
        other_truck=self.fixture._nightly_truck("21"); db.session.commit()
        self.claim(truck=other_truck,own=self.truck.id)
        self.assertIsNone(NeoScorpionTruckDriver.query.filter_by(fuel_truck_id=self.truck.id).one().driver_user_id)
        self.assertEqual(driver_context(self.gateway,self.driver)['selected_driver_truck'].id,other_truck.id)
        self.assertEqual(NeoScorpionTruckDriverAudit.query.count(),3)
        self.assertFalse(self.claim(truck=other_truck,expected=self.driver.id,own=other_truck.id)[0])
        self.assertEqual(NeoScorpionTruckDriverAudit.query.count(),3)

    def test_conflict_stale_write_dispatcher_override_and_clear(self):
        self.claim()
        with self.assertRaisesRegex(ValueError,'changed'):
            self.claim(self.other)
        db.session.rollback()
        with self.assertRaisesRegex(ValueError,'dispatcher'):
            self.claim(self.other,expected=self.driver.id)
        db.session.rollback()
        change_driver(self.gateway,self.operation,self.dispatcher,self.truck.id,self.other.id,
            dispatcher=True,expected_driver_id=self.driver.id,expected_driver_truck_id=None,
            override=True,reason='Driver vehicle handover')
        db.session.commit()
        self.assertIsNone(driver_context(self.gateway,self.driver)['selected_driver_truck'])
        self.assertEqual(driver_context(self.gateway,self.other)['selected_driver_truck'].id,self.truck.id)
        change_driver(self.gateway,self.operation,self.dispatcher,self.truck.id,None,
            dispatcher=True,expected_driver_id=self.other.id,expected_driver_truck_id=None)
        db.session.commit()
        self.assertIsNone(NeoScorpionTruckDriver.query.one().driver_user_id)
        audit=NeoScorpionTruckDriverAudit.query.order_by(NeoScorpionTruckDriverAudit.id.desc()).first()
        self.assertEqual((audit.previous_driver_user_id,audit.driver_user_id,audit.changed_by_user_id),
                         (self.other.id,None,self.dispatcher.id))

    def test_permissions_readonly_and_sort_guard(self):
        self.login(self.driver)
        data=dict(operation_id=self.operation.id,truck_id=self.truck.id,expected_driver_id='',expected_driver_truck_id='')
        headers={'Accept':'application/json'}
        self.assertEqual(self.client.post('/neoscorpion/truck-driver/assignment',data={**data,'manage':'1','driver_user_id':self.other.id},headers=headers).status_code,403)
        self.assertEqual(self.client.post('/neoscorpion/truck-driver/assignment',data={**data,'operation_id':999},headers=headers).status_code,409)
        response=self.client.post('/neoscorpion/truck-driver/assignment',data={**data,'driver_user_id':self.other.id,'actual_left':'99'},headers=headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual(NeoScorpionTruckDriver.query.one().driver_user_id,self.driver.id)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        self.assertTrue(all(t.actual_lbs is None for t in self.work.tank_states))
        watcher=self.fixture._login_user('driver-viewer','watcher'); self.login(watcher)
        for path in ('/neoscorpion/truck-driver','/neoscorpion/truck-driver/revision','/neoscorpion/truck-driver/live-panel'):
            self.assertEqual(self.client.get(path).status_code,200)
        self.assertEqual(self.client.post('/neoscorpion/truck-driver/assignment',data=data,headers=headers).status_code,403)
        self.assertEqual(self.client.post('/neoscorpion/truck-driver/live-panel').status_code,405)
        with patch('app.neonodes.neoscorpion.routes.permission_access',return_value={'can_view':False,'can_edit':False}):
            self.assertEqual(self.client.get('/neoscorpion/truck-driver').status_code,403)

    def test_current_upcoming_off_and_completed_readings_are_canonical(self):
        self.claim()
        for flight,hours in [('UPS902',4),('UPS903',3)]:
            mission=self.fixture._mission(flight,'N492UP',25000,hours)
            assignment=self.fixture._assignment(mission); assignment.assigned_truck_id=self.truck.id
        db.session.commit()
        context=driver_context(self.gateway,self.driver)
        self.assertEqual([i['row']['mission'].id for i in context['driver_groups']['current']],[self.mission.id])
        self.assertEqual([i['row']['mission'].flight_number for i in context['driver_groups']['upcoming']],['UPS903','UPS902'])
        self.assertTrue(all(t['actual_lbs'] is None for t in context['driver_groups']['current'][0]['row']['tank_rows']))
        self.save(self.start+timedelta(minutes=1),**self.final_data()); self.off()
        context=driver_context(self.gateway,self.driver)
        self.assertEqual(context['driver_groups']['current'],[])
        row=context['driver_groups']['previous'][0]['row']
        self.assertEqual(row['physical_work_id'],self.work.id)
        self.assertEqual([t['actual_lbs'] for t in row['tank_rows']],[15000,0,15000])
        self.assertIsNone(self.assignment.completed_at_utc)
        service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id); db.session.commit()
        self.assertTrue(driver_context(self.gateway,self.driver)['driver_groups']['previous'][0]['row']['administratively_complete'])
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)

    def test_repeated_tail_visits_use_independent_reading_and_event_owners(self):
        self.claim(); self._exercise_returning_tail()
        rows=[item['row'] for item in driver_context(self.gateway,self.driver)['driver_groups']['previous']]
        self.assertEqual(len(rows),3)
        self.assertEqual(len({row['physical_work_id'] for row in rows}),3)
        self.assertEqual(sorted(row['transfer_fuel_gallons'] for row in rows),[1000,2000,3000])
        a_rows=[row for row in rows if row['tail_number']=='N491UP']
        self.assertEqual(sorted(sum(t['actual_lbs'] for t in row['tank_rows']) for row in a_rows),[28000,30000])

    def test_new_tables_bootstrap_idempotence_and_database_uniqueness(self):
        self.claim()
        for _ in range(2):
            _create_missing_application_tables(set(inspect(db.engine).get_table_names()))
        self.assertEqual(NeoScorpionTruckDriver.query.count(),1)
        self.assertEqual(NeoScorpionTruckDriverAudit.query.count(),1)
        db.session.add(NeoScorpionTruckDriver(sort_date_operation_id=self.operation.id,fuel_truck_id=self.truck.id,
            driver_user_id=self.other.id,changed_by_user_id=self.dispatcher.id))
        with self.assertRaises(IntegrityError): db.session.flush()
        db.session.rollback()
        self.assertEqual(NeoScorpionTruckDriver.query.one().driver_user_id,self.driver.id)

    def test_board_all_aircraft_layouts_missing_and_measured_zero(self):
        for tail in ('N140UP','N491UP','N312UP','N512UP','N612UP'):
            with self.subTest(tail=tail):
                mission=self.fixture._mission('UPS'+tail[1:4],tail,25000,4)
                assignment=self.fixture._assignment(mission)
                assignment.assigned_fueler_user_id=self.fueler.id
                db.session.commit()
                row=next(item['row'] for item in service.fueling_board_context(self.gateway,self.driver)['board_rows']
                         if item['row']['mission'].id==mission.id)
                self.assertEqual([(t['code'],t['label']) for t in row['tank_rows']],list(service.tank_layout_for_tail(tail)))
                self.assertTrue(all(t['remaining_lbs'] is None and t['actual_lbs'] is None and t['planned_lbs'] is None
                                    for t in row['tank_rows']))
                self.assertTrue(all(t['remaining_display'] in ('','-') and t['actual_display'] in ('','-') for t in row['tank_rows']))
                html=self.fixture.app.jinja_env.get_template('neonodes/neoscorpion/_tank_readings.html').render(row=row)
                self.assertNotIn('<input',html)
                self.assertIn('Remaining',html); self.assertIn('Planned',html); self.assertIn('Actual',html)
        self.save(self.start+timedelta(minutes=1),**self.final_data())
        row=next(item['row'] for item in service.fueling_board_context(self.gateway,self.driver)['board_rows']
                 if item['row']['mission'].id==self.mission.id)
        center=next(t for t in row['tank_rows'] if t['code']=='ctr')
        self.assertEqual(center['actual_lbs'],0)
        self.assertNotEqual(center['actual_display'],'-')
