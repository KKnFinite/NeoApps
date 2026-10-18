"""Integrated asset/driver presentation reuses existing permission and mutation contracts."""
from html.parser import HTMLParser
from unittest import TestCase
from unittest.mock import patch
from app.extensions import db
from app.models import NeoScorpionFuelTruck,NeoScorpionSortTruck,NeoScorpionTruckDriver,NeoScorpionVendorAccount
from app.services import neoscorpion_vendors as vendors
from tests import test_neoscorpion_vendor_workflow as base


class FormActions(HTMLParser):
    def __init__(self,html):
        super().__init__();self.actions=set();self.depth=0;self.nested=False;self.feed(html)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='form':self.nested|=bool(self.depth);self.depth+=1
        if attrs.get('name')=='action' and attrs.get('value'):self.actions.add(attrs['value'])
    def handle_endtag(self,tag):
        if tag=='form':self.depth-=1


class AssetsWorkspaceTest(TestCase):
    setUp=base.VendorWorkflowTest.setUp
    prepare=base.VendorWorkflowTest.prepare
    login=base.VendorWorkflowTest.login
    off=base.VendorWorkflowTest.off
    vendor=base.VendorWorkflowTest.vendor

    def post(self,path,**data):
        return self.client.post(path,data=dict(dispatch_assets_workspace='1',operation_id=self.operation.id,**data))

    def test_every_driver_management_action_is_present_without_disconnected_navigation(self):
        self.prepare();person,account,_=self.vendor()
        unregistered,_=vendors.invite(self.gateway,self.dispatcher,'pending-registration@example.test','Pending Registration')
        claim=vendors.request_claim(account,self.operation,self.truck.id);db.session.commit()
        self.login(self.dispatcher)
        old=self.client.get('/neoscorpion/driver/manage').get_data(as_text=True)
        response=self.client.get('/neoscorpion/fuel-dispatch?assets=open')
        self.assertEqual(response.status_code,200)
        html=response.get_data(as_text=True);old_forms,new_forms=FormActions(old),FormActions(html)
        self.assertTrue(old_forms.actions.issubset(new_forms.actions),old_forms.actions-new_forms.actions)
        self.assertFalse(new_forms.nested)
        self.assertIn('data-assets-open="'+str(self.truck.id)+'"',html)
        self.assertNotIn('href="/neoscorpion/driver/manage"',html)
        self.assertIn('id="nightly-truck-'+str(self.truck.id)+'"',html)
        for action in ('set_islands','set_fueler_nickname','remove_fueler','add_fueler',
                       'update_truck','mark_topping_off','remove_truck','add_truck','correct'):
            # Empty Fueler rosters have no edit/remove controls by design.
            if action not in ('set_fueler_nickname','remove_fueler'):self.assertIn(action,new_forms.actions)
        self.assertIn('Pending Registration',html)
        self.assertEqual(NeoScorpionSortTruck.query.count(),1)

    def test_inline_approval_claim_manual_name_and_disconnect_preserve_inventory(self):
        self.prepare();person,account,_=self.vendor()
        claim=vendors.request_claim(account,self.operation,self.truck.id);db.session.commit()
        self.login(self.dispatcher)
        response=self.post('/neoscorpion/driver/manage',action='claim_approve',request_id=claim.id,version=claim.version)
        self.assertEqual(response.status_code,200,response.get_data(as_text=True));self.assertTrue(response.json['ok'])
        connection=NeoScorpionTruckDriver.query.one();self.assertEqual(connection.driver_user_id,person.id)
        response=self.post('/neoscorpion/driver/manage',action='manual',truck_id=self.truck.id,
            expected_driver_id=person.id,name='Manual Contractor')
        self.assertEqual(response.status_code,200)
        self.assertIsNone(connection.driver_user_id);self.assertEqual(connection.manual_name,'Manual Contractor')
        response=self.post('/neoscorpion/driver/manage',action='assign',truck_id=self.truck.id,user_id=person.id,
            expected_driver_id='',expected_driver_truck_id='')
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        response=self.post('/neoscorpion/driver/manage',action='assign',truck_id=self.truck.id,user_id='',
            expected_driver_id=person.id,expected_driver_truck_id='')
        self.assertEqual(response.status_code,200)
        self.assertIsNone(connection.driver_user_id);self.assertEqual(connection.manual_name,'')
        self.assertEqual(self.selection.starting_gallons,10000);self.assertEqual(self.selection.current_gallons,10000)
        self.assertEqual(self.assignment.assigned_truck_id,self.truck.id)

    def test_inline_assets_are_sort_scoped_and_errors_are_not_false_success(self):
        self.login(self.dispatcher)
        response=self.post('/neoscorpion/fuel-dispatch/assets',action='set_islands',fuel_island_count='2')
        self.assertEqual(response.status_code,200);self.assertTrue(response.json['ok'])
        bad=self.client.post('/neoscorpion/fuel-dispatch/assets',data=dict(dispatch_assets_workspace='1',
            operation_id=self.operation.id+999,action='set_islands',fuel_island_count='3'))
        self.assertEqual(bad.status_code,400);self.assertFalse(bad.json['ok']);self.assertIn('sort changed',bad.json['error'])
        self.login(self.fueler)
        denied=self.post('/neoscorpion/fuel-dispatch/assets',action='set_islands',fuel_island_count='4')
        self.assertEqual(denied.status_code,403);self.assertFalse(denied.json['ok'])

    def test_invitation_failure_rolls_back_and_success_publishes_revision(self):
        self.login(self.dispatcher)
        with patch('app.services.email_service.send_vendor_invitation',return_value={'sent':False}):
            response=self.post('/neoscorpion/driver/manage',action='invite',email='failed@example.test',name='Failed Delivery')
        self.assertEqual(response.status_code,400);self.assertFalse(response.json['ok'])
        self.assertEqual(NeoScorpionVendorAccount.query.count(),0)
        with patch('app.services.email_service.send_vendor_invitation',return_value={'sent':True}):
            response=self.post('/neoscorpion/driver/manage',action='invite',email='success@example.test',name='Invited Driver')
        self.assertEqual(response.status_code,200,response.get_data(as_text=True));self.assertTrue(response.json['ok'])
        self.assertGreater(response.json['revision'],0)

    def test_vendor_and_readonly_users_cannot_gain_administration(self):
        self.prepare();person,account,_=self.vendor()
        self.login(self.dispatcher)
        with patch('app.neonodes.neoscorpion.routes.permission_access',return_value={'can_view':True,'can_edit':False}):
            html=self.client.get('/neoscorpion/fuel-dispatch').get_data(as_text=True)
        self.assertNotIn('SEND INVITATION',html);self.assertNotIn('APPROVE TONIGHT',html)
        self.client.post('/logout')
        self.client.post('/login',data={'email':person.email,'password':'VendorPassword123!'})
        self.assertEqual(self.client.get('/neoscorpion/fuel-dispatch').status_code,403)
        self.assertEqual(self.post('/neoscorpion/driver/manage',action='disable',user_id=person.id).status_code,403)
