"""R03J semantic OFF eligibility, authority, final entry and immutable completion."""
import json
import unittest
from datetime import datetime
from unittest.mock import patch
from app.extensions import db
from app.models import NeoScorpionFuelingEvent
from app.services import neoscorpion as fuel
from tests import test_neoscorpion_off_contract as fixtures
from tests import neoscorpion_off_workflow as workflow
from tests import neoscorpion_off_presentation as ui


class OffPresentationContractTest(unittest.TestCase):
    setUp=fixtures.PhysicalOffContractTest.setUp

    def ready(self,*,truck=True):
        self.mission.planned_fuel_load=31000;db.session.commit()
        if truck:self.truck,self.stock=workflow.assign_truck(self.fixture,self.assignment,self.user,self.dispatcher)
        self.work=fuel.save_fueler_entry(self.gateway,self.user,dict(assignment_id=str(self.assignment.id),
            apu_running='no',remaining_left='10',remaining_ctr='10',remaining_right='10',
            actual_left='11',actual_ctr='10',actual_right='10'),now_utc=datetime(2026,8,18,3,45)).fuel_work_state
        db.session.commit();self.fixture._login(self.user)

    def request_as(self,actor,path,*,data=None):
        email=actor.email;app=self.fixture.app
        with app.app_context():
            client=app.test_client();self.assertEqual(client.post('/login',data={'email':email,'password':'TestPassword123!'}).status_code,302)
            return client.get(path) if data is None else client.post(path,data=data,headers={'Accept':'application/json'})

    def test_truckless_warning_tracks_actual_no_transfer_and_positive_transfer_guards(self):
        self.ready(truck=False)
        for transfer,reason in (('150','Assign the truck used for positive T/F'),('', 'No physical truck')):
            fuel.save_fueler_entry(self.gateway,self.user,dict(assignment_id=str(self.assignment.id),transfer_fuel_gallons=transfer));db.session.commit()
            nodes=ui.read_views(self.fixture,self.assignment);before=ui.snapshot()
            for node in nodes:form=ui.off_form(self,node,self.assignment,enabled=False,reason=reason)
            rejected=self.fixture.client.post(form.attrs['action'],data=form.values())
            self.assertEqual(rejected.status_code,400);self.assertIn(reason,rejected.json['error'])
            self.assertEqual(ui.snapshot(),before)
        self.assertTrue(fuel.fueler_context(self.gateway,self.user,assignment_id=self.assignment.id)['rows'][0]['fuel_on_board_ready'])
        fuel.complete_fuel_on_board(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assertEqual((self.work.manual_on_at_utc,self.work.off_at_utc),(None,None))
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_read_only_foreign_stale_and_csrf_requests_cannot_act_on_off(self):
        self.ready();other=self.fixture._add_user('off_read_only_dispatch','operator');db.session.commit()
        node=ui.read_views(self.fixture,self.assignment)[0];form=ui.off_form(self,node,self.assignment,enabled=True)
        before=ui.snapshot();path=form.attrs['action'];values=form.values();expected=json.loads(values['expected'])
        stale={**expected,'work_ref':self.work.id+999}
        rejected=self.fixture.client.post(path,data={**values,'expected':json.dumps(stale)})
        self.assertEqual(rejected.status_code,409);self.assertEqual(ui.snapshot(),before)
        rejected=self.request_as(other,path,data=values)
        self.assertEqual(rejected.status_code,400);self.assertEqual(rejected.json['error'],'This physical segment belongs to another Fueler.')
        readonly=self.request_as(other,f'/neoscorpion/fuel-dispatch/fueler-data/{self.assignment.id}')
        self.assertEqual(readonly.status_code,200)
        card=ui.card(self,readonly.json['html'],self.assignment)
        self.assertEqual(card.select('form',css_class='neoscorpion-fueler-off-form'),[])
        denied=self.request_as(other,f'/neoscorpion/fuel-dispatch/fueler-data/{self.assignment.id}/off',data=values)
        self.assertEqual(denied.status_code,403)
        missing=self.fixture.client.post('/neoscorpion/fueler/assignments/999999/off',data=values)
        self.assertEqual(missing.status_code,400)
        with patch.dict(self.fixture.app.config,{'CSRF_PROTECT_TESTING':True}):
            denied=self.fixture.client.post(path,data={'expected':values['expected']})
            self.assertEqual(denied.status_code,400)
        self.assertEqual(ui.snapshot(),before)

    def test_post_off_actual_entry_and_completed_open_view_preserve_event_and_clock(self):
        self.ready();off_at=datetime(2026,8,18,4)
        fuel.mark_fueler_off(self.gateway,self.user,self.assignment.id,now_utc=off_at);db.session.commit()
        node=ui.read_views(self.fixture,self.assignment)[0];ui.confirmed_off(self,node,'23:00')
        forms=node.select('form',attrs={'data-fuel-autosave':None});self.assertEqual(len(forms),1)
        form=forms[0];data={**form.values(),'actual_left':'12','transfer_fuel_gallons':'300'}
        with patch.object(db.session,'commit',wraps=db.session.commit) as commit:
            result=self.fixture.client.post(form.attrs['action'],data=data)
            self.assertEqual(commit.call_count,1)
        self.assertEqual(result.status_code,200)
        self.assertEqual((self.work.off_at_utc,self.work.off_by_user_id),(off_at,self.user.id))
        self.assertEqual(next(t.actual_lbs for t in self.work.tank_states if t.tank_code=='left'),12000)
        self.assertIsNone(self.work.fueler_submitted_at_utc)
        current=ui.card(self,result.json['html'],self.assignment);ui.confirmed_off(self,current,'23:00')
        updated=current.select('form',attrs={'data-fuel-autosave':None})[0]
        before=ui.snapshot()
        with patch.object(db.session,'commit',wraps=db.session.commit) as commit:
            repeated=self.fixture.client.post(updated.attrs['action'],data={**updated.values(),'actual_left':'12','transfer_fuel_gallons':'300'})
            self.assertEqual(commit.call_count,0)
        self.assertEqual(repeated.status_code,200);self.assertEqual(ui.snapshot(),before)
        workflow.submit(self.fixture,self.assignment,self.user)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0);self.assertEqual(self.stock.current_gallons,1000)
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assertEqual(self.stock.current_gallons,700);self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        frozen=ui.snapshot()
        view=self.request_as(self.dispatcher,f'/neoscorpion/fuel-dispatch/fueler-data/{self.assignment.id}')
        self.assertEqual(view.status_code,200);node=ui.card(self,view.json['html'],self.assignment)
        ui.confirmed_off(self,node,'23:00')
        self.assertTrue(node.select(attrs={'data-completion-evidence':None}))
        self.assertEqual(node.select('form',attrs={'data-fuel-autosave':None}),[])
        rejected=self.fixture.client.post(form.attrs['action'],data={**data,'actual_left':'13'})
        self.assertEqual(rejected.status_code,409);self.assertEqual(ui.snapshot(),frozen)
        row=fuel.fueler_context(self.gateway,self.user,assignment_id=self.assignment.id,include_completed=True)['rows'][0]
        rejected=self.fixture.client.post(form.attrs['action'],data={**data,'expected':json.dumps(row['edit_baseline']),'actual_left':'13'})
        self.assertEqual(rejected.status_code,400);self.assertIn('Completed fuel assignments',rejected.json['error'])
        self.assertEqual(ui.snapshot(),frozen)

    def test_failed_off_rolls_back_and_restores_the_same_enabled_action(self):
        self.ready();before=ui.snapshot()
        with patch.object(fuel,'record_nightly_operational_change',side_effect=RuntimeError('synthetic OFF rollback')):
            with self.assertRaisesRegex(RuntimeError,'synthetic OFF rollback'):
                fuel.mark_fueler_off(self.gateway,self.user,self.assignment.id,now_utc=datetime(2026,8,18,4))
        db.session.rollback();self.assertEqual(ui.snapshot(),before)
        node=ui.read_views(self.fixture,self.assignment)[0];form=ui.off_form(self,node,self.assignment,enabled=True)
        result=self.fixture.client.post(form.attrs['action'],data=form.values())
        self.assertEqual(result.status_code,200);self.assertTrue(result.json['ok'])
        self.assertIsNotNone(self.work.off_at_utc);self.assertEqual(self.work.off_by_user_id,self.user.id)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0);self.assertEqual(self.stock.current_gallons,1000)
