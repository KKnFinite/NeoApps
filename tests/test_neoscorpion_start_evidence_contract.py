"""R03I: physical authority, conservative review, travel, and atomic rejection."""
import copy
import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import event
from app.extensions import db
from app.models import NeoScorpionFuelWorkState, NeoScorpionSortAssetState, SortDateOperation
from app.services import neoscorpion as fuel
from tests import test_neoscorpion_fueling_start as fixtures
from tests import neoscorpion_start_workflow as workflow
from tests.test_neoscorpion_fob_evidence_contract import snapshot as work_snapshot


def snapshot(case):
    return dict(work=work_snapshot(case),assets=copy.deepcopy([
        fuel._fuel_rollback_values(s) for s in NeoScorpionSortAssetState.query.all()]))


class StartEvidenceContractTest(unittest.TestCase):
    setUp=fixtures.FuelingStartHotfixTest.setUp
    prepare=fixtures.FuelingStartHotfixTest.prepare
    save=fixtures.FuelingStartHotfixTest.save
    readings=fixtures.FuelingStartHotfixTest.readings
    row=fixtures.FuelingStartHotfixTest.row
    confirm=fixtures.FuelingStartHotfixTest.confirm

    def test_expired_travel_and_read_only_projections_never_start_or_write(self):
        self.prepare();prior,old=workflow.previous_physical(self);workflow.release(self,prior)
        work=self.save(**self.readings()).fuel_work_state;before=snapshot(self);writes=[]
        def record(_conn,_cursor,statement,*args):
            if statement.lstrip().split(' ',1)[0].upper() in ('INSERT','UPDATE','DELETE') and 'neoscorpion_' in statement.lower():writes.append(statement)
        event.listen(db.engine,'before_cursor_execute',record)
        try:
            for minutes,reason in ((1,'COMING FROM ALPHA · ~1 MIN'),(3,'TRUCK ON PENDING'),(1440,'TRUCK ON PENDING')):
                row=self.row(self.now+timedelta(minutes=minutes))
                self.assertEqual(row['truck_start_reason'],reason)
                self.assertEqual(row['dispatch_status_key'],'assigned')
                self.assertEqual(row['status_review_reasons'],())
                self.assertFalse(row['fueling_on_trusted']);self.assertFalse(row['fueling_on_active'])
                self.assertIsNone(row['fueling_effective_on_utc'])
                self.assertIsNone(fuel.active_fueler_off(row,self.operation,fuel.assignment_planning_settings(self.gateway,settings=self.settings)))
        finally:event.remove(db.engine,'before_cursor_execute',record)
        self.assertEqual(writes,[]);self.assertEqual(snapshot(self),before)
        self.assertEqual((work.on_at_utc,work.manual_on_at_utc,work.off_at_utc),(None,)*3)
        self.assertEqual(old.off_at_utc,self.now)

    def test_unknown_release_location_remains_unknown_until_actual_confirmation(self):
        self.prepare();prior,old=workflow.previous_physical(self,position=None);workflow.release(self,prior)
        work=self.save(**self.readings()).fuel_work_state;before=copy.deepcopy(fuel._fuel_rollback_values(old))
        row=self.row(self.now+timedelta(hours=1))
        self.assertEqual(row['truck_start_reason'],'TRUCK LOCATION UNKNOWN')
        self.assertEqual(row['truck_travel_origin'],'');self.assertEqual(row['truck_travel_until'],'')
        self.assertEqual(row['dispatch_status_key'],'assigned');self.assertFalse(row['fueling_on_trusted'])
        self.confirm(now=self.now+timedelta(hours=1))
        row=self.row(self.now+timedelta(hours=1))
        self.assertTrue(row['manual_on_confirmed']);self.assertTrue(row['fueling_on_trusted'])
        self.assertEqual(row['fueling_effective_on_utc'],work.manual_on_at_utc)
        self.assertEqual(fuel._fuel_rollback_values(old),before)

    def test_planned_other_assignment_without_physical_on_does_not_occupy_truck(self):
        self.prepare();planned=workflow.plan_other(self)
        self.save(**self.readings());self.assertEqual(self.row()['dispatch_status_key'],'assigned')
        self.assertEqual(self.row()['truck_start_reason'],'')
        self.assertTrue(self.confirm()[0])
        self.assertEqual(self.row()['dispatch_status_key'],'fueling')
        self.assertIsNone(planned.active_work_state_id)

    def post(self,actor,path,data):
        email=actor.email;app=self.fixture.app
        with app.app_context():
            client=app.test_client();self.assertEqual(client.post('/login',data={'email':email,'password':'TestPassword123!'}).status_code,302)
            return client.post(path,data=data,headers={'Accept':'application/json'})

    def test_stale_work_wrong_truck_cycle_permission_csrf_and_sort_reject_atomically(self):
        self.prepare();work=self.save(**self.readings()).fuel_work_state;before=snapshot(self)
        path=f'/neoscorpion/fueler/assignments/{self.assignment.id}/on'
        valid=dict(expected_cycle='1',expected_tail='N491UP',expected_truck_id=str(self.truck.id),expected_work_ref=str(work.id))
        for fields,status,error in (({'expected_work_ref':str(work.id+100)},409,'changed'),
                                    ({'expected_cycle':'2'},400,'changed'),
                                    ({'expected_truck_id':str(self.truck.id+100)},400,'truck changed')):
            with self.subTest(fields=fields):
                result=self.post(self.fueler,path,{**valid,**fields})
                self.assertEqual(result.status_code,status)
                self.assertIn(error,result.json['error'].lower())
                self.assertEqual(snapshot(self),before)
        denied=self.post(self.dispatcher,path,valid)
        self.assertNotEqual(denied.status_code,200);self.assertFalse(denied.json['ok'])
        with self.fixture.app.app_context():
            client=self.fixture.app.test_client();client.post('/login',data={'email':self.fueler.email,'password':'TestPassword123!'})
            with patch.dict(self.fixture.app.config,{'CSRF_PROTECT_TESTING':True}):
                self.assertEqual(client.post(path,data=valid).status_code,400)
        other=SortDateOperation(gateway_id=self.gateway.id,gateway_code=self.gateway.code,
            sort_name='night',sort_date=self.operation.sort_date-timedelta(days=1))
        db.session.add(other);db.session.commit()
        with patch.object(fuel,'current_sort_operation',return_value=other):
            with self.assertRaisesRegex(ValueError,'not found'):
                self.confirm()
        db.session.rollback();self.assertEqual(snapshot(self),before)

    def test_on_failure_after_physical_snapshot_rolls_back_before_retry(self):
        self.prepare();work=self.save(**self.readings()).fuel_work_state;before=snapshot(self)
        with patch.object(fuel,'_capture_physical_forecast',side_effect=RuntimeError('synthetic forecast failure')):
            with self.assertRaisesRegex(RuntimeError,'synthetic forecast failure'):self.confirm()
        db.session.rollback();self.assertEqual(snapshot(self),before)
        self.assertTrue(self.confirm()[0]);self.assertEqual(work.manual_on_at_utc,self.now)
        self.assertFalse(self.confirm(now=self.now+timedelta(minutes=1))[0])

    def test_conflicting_raw_legacy_on_cannot_erase_genuine_prior_physical_work(self):
        self.prepare();prior,old=workflow.previous_physical(self)
        work=self.save(**self.readings()).fuel_work_state
        # Explicit contradictory historical import; not a new canonical ON.
        work.on_at_utc=self.now-timedelta(minutes=20);db.session.commit()
        before=snapshot(self);prior_facts=copy.deepcopy((fuel._fuel_rollback_values(prior),fuel._fuel_rollback_values(old)))
        with self.assertRaisesRegex(ValueError,'conflicting mission; dispatcher review required'):
            workflow.release(self,prior)
        db.session.rollback()
        self.assertEqual(snapshot(self),before)
        self.assertEqual((fuel._fuel_rollback_values(prior),fuel._fuel_rollback_values(old)),prior_facts)
        self.assertEqual(self.row()['dispatch_status_key'],'review')
        self.assertFalse(self.row()['fueling_on_trusted']);self.assertIsNone(old.off_at_utc)
