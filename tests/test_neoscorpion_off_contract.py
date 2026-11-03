"""Physical OFF identity, authorization and atomic rollback with canonical ON."""
import copy
import json
import re
import unittest
from datetime import date, datetime
from unittest.mock import patch

from app.extensions import db
from app.models import NeoScorpionFuelAuditEntry, NeoScorpionFuelingEvent, SortDateOperation
from app.services import neoscorpion as fuel
from tests import test_neoscorpion_fueler_off as fixtures
from tests import neoscorpion_off_workflow as workflow


class PhysicalOffContractTest(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.NeoScorpionFuelerOffTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.gateway=self.fixture.gateway;self.user=self.fixture.user
        self.dispatcher=self.fixture._add_user('r03f_dispatcher','simulator')
        db.session.commit()
        self.operation,self.mission,self.assignment=self.fixture._assignment()

    def facts(self,work,stock):
        return copy.deepcopy((fuel._fuel_rollback_values(work),fuel._fuel_rollback_values(stock),
            workflow.revision(self.operation),fuel._fuel_rollback_values(self.assignment),
            [fuel._fuel_rollback_values(a) for a in NeoScorpionFuelAuditEntry.query.order_by(NeoScorpionFuelAuditEntry.id)],
            NeoScorpionFuelingEvent.query.count()))

    def test_truck_on_identity_csrf_and_foreign_requests(self):
        saved=fuel.save_fueler_entry(self.gateway,self.user,dict(assignment_id=self.assignment.id,
            apu_running='no',remaining_left='10',remaining_ctr='10',remaining_right='10',
            actual_left='11',actual_ctr='10',actual_right='10',transfer_fuel_gallons='150'))
        db.session.commit();work=saved.fuel_work_state
        with self.assertRaisesRegex(ValueError,'Assign the truck used for positive T/F before OFF'):
            fuel.mark_fueler_off(self.gateway,self.user,self.assignment.id)
        db.session.rollback();self.assertIsNone(work.off_at_utc)
        truck,stock=workflow.assign_truck(self.fixture,self.assignment,self.user,self.dispatcher,start=False)
        before=self.facts(work,stock)
        with self.assertRaisesRegex(ValueError,'Confirm physical TRUCK ON before TRUCK OFF'):
            fuel.mark_fueler_off(self.gateway,self.user,self.assignment.id)
        db.session.rollback();self.assertEqual(self.facts(work,stock),before)
        fuel.confirm_fueler_truck_on(self.gateway,self.user,self.assignment.id,expected_cycle=1,
            expected_tail=self.mission.assigned_tail_number,expected_truck_id=truck.id,
            now_utc=datetime(2026,8,18,3,30));db.session.commit()
        before=self.facts(work,stock)
        other=self.fixture._add_user('r03f_wrong_fueler','operator');db.session.commit()
        for actor,aid in ((other,self.assignment.id),(self.user,self.assignment.id+999)):
            with self.assertRaisesRegex(ValueError,'Fuel assignment was not found for this fueler'):
                fuel.mark_fueler_off(self.gateway,actor,aid)
            db.session.rollback();self.assertEqual(self.facts(work,stock),before)
        self.fixture._login(self.user)
        self.fixture.app.config['CSRF_PROTECT_TESTING']=True
        endpoint=f'/neoscorpion/fueler/assignments/{self.assignment.id}/off'
        self.assertEqual(self.fixture.client.post(endpoint,data={}).status_code,400)
        page=self.fixture.client.get('/neoscorpion/fueler')
        self.assertEqual(page.status_code,200,page.location)
        token=re.search(r'<meta name="csrf-token" content="([^"]+)"',page.get_data(as_text=True)).group(1)
        row=fuel.fueler_context(self.gateway,self.user,assignment_id=self.assignment.id)['rows'][0]
        expected={**row['edit_baseline'],'work_ref':work.id+999}
        response=self.fixture.client.post(endpoint,data={'expected':json.dumps(expected)},
            headers={'X-CSRFToken':token,'X-Requested-With':'XMLHttpRequest'})
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.facts(work,stock),before)
        self.fixture.app.config['CSRF_PROTECT_TESTING']=False
        other_op=SortDateOperation(generated_by_user_id=self.dispatcher.id,gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,sort_date=date(2026,8,18),sort_name='night',window_minutes=60)
        db.session.add(other_op);db.session.commit()
        self.fixture.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE']=datetime(2026,8,18,22)
        with self.assertRaisesRegex(ValueError,'Fuel assignment was not found for this fueler'):
            fuel.mark_fueler_off(self.gateway,self.user,self.assignment.id)
        db.session.rollback()
        self.fixture.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE']=datetime(2026,8,17,22)
        self.assertEqual(self.facts(work,stock),before)

    def test_physical_off_failure_rolls_back_and_retries_once(self):
        truck,stock=workflow.assign_truck(self.fixture,self.assignment,self.user,self.dispatcher)
        work=fuel.current_work(self.assignment,self.mission.assigned_tail_number)
        before=self.facts(work,stock)
        with patch.object(fuel,'record_nightly_operational_change',side_effect=RuntimeError('Injected OFF failure')):
            with self.assertRaisesRegex(RuntimeError,'Injected OFF failure'):
                fuel.mark_fueler_off(self.gateway,self.user,self.assignment.id,now_utc=datetime(2026,8,18,4))
        db.session.rollback();db.session.expire_all()
        self.assertEqual(self.facts(work,stock),before)
        result=fuel.mark_fueler_off(self.gateway,self.user,self.assignment.id,now_utc=datetime(2026,8,18,4))
        db.session.commit();self.assertTrue(result.changed)
        after=self.facts(work,stock)
        repeated=fuel.mark_fueler_off(self.gateway,self.user,self.assignment.id,now_utc=datetime(2026,8,18,4,10))
        db.session.commit();self.assertFalse(repeated.changed)
        self.assertEqual(self.facts(work,stock),after)
        self.assertEqual((work.off_at_utc,work.off_by_user_id),(datetime(2026,8,18,4),self.user.id))
        self.assertEqual(stock.current_gallons,1000)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
