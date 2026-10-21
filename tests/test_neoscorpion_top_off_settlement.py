"""Stale card actions must not cross an operational sort or TOP OFF trip."""
import unittest
from app.extensions import db
from app.models import NeoScorpionSortTruck, NeoScorpionTopOffEvent
from app.services import neoscorpion_assets as assets
from tests import test_neoscorpion_nightly_asset_routes as fixtures


class TopOffSettlementTest(unittest.TestCase):
    setUp = fixtures.NeoScorpionNightlyAssetRoutesTest.setUp
    tearDown = fixtures.NeoScorpionNightlyAssetRoutesTest.tearDown
    _login_user = fixtures.NeoScorpionNightlyAssetRoutesTest._login_user
    _add_approved_user = fixtures.NeoScorpionNightlyAssetRoutesTest._add_approved_user
    _add_operation = fixtures.NeoScorpionNightlyAssetRoutesTest._add_operation
    _add_truck = fixtures.NeoScorpionNightlyAssetRoutesTest._add_truck

    def prepare(self):
        from datetime import date
        self._login_user('top-off-dispatcher','simulator')
        self.operation=self._add_operation(date(2026,8,17))
        self.truck=self._add_truck('SAFE TOP OFF',capacity=10000)
        db.session.flush()
        self.selection=NeoScorpionSortTruck(sort_date_operation_id=self.operation.id,
            fuel_truck_id=self.truck.id,status='available',starting_gallons=6000,current_gallons=6000,inventory_verified=True)
        db.session.add(self.selection);db.session.commit()

    def post(self,action,token,**extra):
        return self.client.post('/neoscorpion/fuel-dispatch/assets',data={
            'dispatch_truck_card':'1','action':action,'fuel_truck_id':self.truck.id,
            'operation_id':self.operation.id,'top_off_action_state':token,**extra})

    def test_duplicate_timeout_replay_cannot_cross_trips(self):
        self.prepare();sent_token=assets.top_off_action_state(self.selection)
        response=self.post('mark_topping_off',sent_token)
        self.assertEqual(response.status_code,200);self.assertTrue(response.json['ok'])
        db.session.refresh(self.selection);return_token=assets.top_off_action_state(self.selection)
        self.assertEqual(self.post('mark_topping_off',sent_token).status_code,400)
        self.assertEqual(self.post('complete_top_off',return_token,current_gallons=9000).status_code,200)
        db.session.refresh(self.selection);epoch=self.selection.inventory_epoch
        self.assertEqual(self.selection.status,'available');self.assertEqual(self.selection.current_gallons,9000)
        self.assertEqual(self.post('mark_topping_off',sent_token).status_code,400)
        self.assertEqual(self.post('complete_top_off',return_token,current_gallons=9000).status_code,400)
        self.assertEqual(self.post('mark_topping_off',assets.top_off_action_state(self.selection)).status_code,200)
        self.assertEqual(self.post('complete_top_off',return_token,current_gallons=1000).status_code,400)
        db.session.refresh(self.selection)
        self.assertEqual(self.selection.status,'topping_off');self.assertEqual(self.selection.current_gallons,9000)
        self.assertEqual(self.selection.inventory_epoch,epoch)
        self.assertEqual(NeoScorpionTopOffEvent.query.count(),3)

    def test_missing_token_wrong_sort_invalid_gallons_and_unauthorized(self):
        self.prepare();token=assets.top_off_action_state(self.selection)
        self.assertEqual(self.post('mark_topping_off','').status_code,400)
        self.assertEqual(self.post('mark_topping_off',token,operation_id=999999).status_code,400)
        self.assertEqual(NeoScorpionTopOffEvent.query.count(),0)
        self.assertEqual(self.post('mark_topping_off',token).status_code,200)
        db.session.refresh(self.selection);token=assets.top_off_action_state(self.selection)
        for gallons in ('','-1','10001','garbage'):
            self.assertEqual(self.post('complete_top_off',token,current_gallons=gallons).status_code,400)
        self.assertEqual(NeoScorpionTopOffEvent.query.count(),1)
        self._login_user('top-off-denied','watcher')
        self.assertEqual(self.post('complete_top_off',token,current_gallons=8000).status_code,403)
