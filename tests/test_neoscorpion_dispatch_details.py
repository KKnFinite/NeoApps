"""Presentation-only console: preserve existing forms, permissions and visit identities."""
from collections import Counter
from datetime import timedelta
from html.parser import HTMLParser
import subprocess
import unittest
from unittest.mock import patch
from jinja2 import ChoiceLoader, DictLoader
from app.extensions import db
from app.services import neoscorpion as fuel
from tests import test_neoscorpion_continuations as base


class Controls(HTMLParser):
    def __init__(self, html):
        super().__init__(); self.controls=Counter(); self.depth=0; self.nested=False
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag=='form':
            self.nested |= self.depth>0; self.depth+=1
            self.controls[('form',attrs.get('action'))]+=1
        if tag in ('input','textarea','select') and attrs.get('name')!='csrf_token':
            self.controls[(tag,attrs.get('name'),attrs.get('form'))]+=1
        if tag=='button' and attrs.get('formaction'):
            self.controls[('button',attrs['formaction'])]+=1
    def handle_endtag(self, tag):
        if tag=='form':self.depth-=1


class DetailsTest(unittest.TestCase):
    setUp=base.ContinuationTest.setUp
    prepare=base.ContinuationTest.prepare
    save=base.ContinuationTest.save
    off=base.ContinuationTest.off
    final_data=base.ContinuationTest.final_data
    baseline=base.ContinuationTest.baseline
    row=base.ContinuationTest.row
    submit=base.ContinuationTest.submit
    login=base.ContinuationTest.login
    plan=base.ContinuationTest.plan
    target=base.ContinuationTest.target
    handoff=base.ContinuationTest.handoff
    finish_child=base.ContinuationTest.finish_child

    def html(self):
        self.login(self.dispatcher)
        result=self.client.get('/neoscorpion/fuel-dispatch')
        self.assertEqual(result.status_code,200)
        return result.get_data(as_text=True)

    def test_all_original_controls_survive_reorganization_without_nested_forms(self):
        self.handoff()
        current=self.html()
        templates={}
        for name in ('_fuel_dispatch_panel.html','_dispatch_action_center.html','_dispatch_work_timeline.html','_tail_notes.html'):
            path='app/templates/neonodes/neoscorpion/'+name
            templates['neonodes/neoscorpion/'+name]=subprocess.check_output(['git','show','1b69766d:'+path]).decode()
        app=self.client.application
        loader=app.jinja_env.loader
        try:
            app.jinja_env.loader=ChoiceLoader([DictLoader(templates),loader]);app.jinja_env.cache.clear()
            original=self.html()
        finally:
            app.jinja_env.loader=loader;app.jinja_env.cache.clear()
        old,new=Controls(original),Controls(current)
        self.assertFalse(new.nested)
        self.assertEqual(old.controls,new.controls)
        self.assertEqual(current.count('data-details-region="fuel"'),1)
        self.assertEqual(current.count('data-details-region="notes"'),1)
        self.assertIn('Source correction / review reason',current)
        self.assertIn('data-console-section="source-',current)
        self.assertIn('physical_work_id='+str(self.work.id),current)
        self.assertIn('name="expected_submission_version"',current)

    def test_separate_visit_notes_and_measurements_and_finalized_history(self):
        p,child=self.handoff()
        self.work.notes='Truck A verified note';child.notes='Truck B preparation note';db.session.commit()
        html=self.html()
        for work,note in ((self.work,'Truck A verified note'),(child,'Truck B preparation note')):
            self.assertIn('data-visit-note="'+str(work.id)+'"',html);self.assertIn(note,html)
        self.assertIn('T/F 3000 gal',html)
        self.assertIn('T/F - gal',html)
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id,
            physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number);db.session.commit()
        html=self.html()
        history=html.split('data-console-section="history"',1)[1]
        self.assertIn('data-console-visit="'+str(self.work.id)+'"',history)
        self.assertIn('VERIFIED',history)
        timeline=html.split('data-dispatch-work-timeline',1)[1].split('data-details-region="fuel"',1)[0]
        self.assertNotIn('data-console-visit="'+str(self.work.id)+'"',timeline)

    def test_view_only_console_contains_no_optional_source_mutation(self):
        self.handoff()
        self.login(self.dispatcher)
        with patch('app.neonodes.neoscorpion.routes.permission_access',return_value={'can_view':True,'can_edit':False}):
            html=self.client.get('/neoscorpion/fuel-dispatch').get_data(as_text=True)
        self.assertNotIn('>REVIEW SOURCE HANDOFF</button>',html)
        self.assertNotIn('>VERIFY TRUCK ',html)
        self.assertNotIn('data-tail-note-form',html)
        self.assertIn('Truck',html)

    def test_call_dispatch_ack_is_single_direct_action_and_resolves_independently(self):
        alert=self.row()['call_dispatch_alert']
        self.assertIsNotNone(alert)
        html=self.html()
        center=html.split('data-dispatch-action-center',1)[1].split('data-dispatch-work-timeline',1)[0]
        self.assertIn('data-action-key="call-dispatch"',center)
        self.assertEqual(html.count('action="/neoscorpion/fuel-dispatch/call-dispatch/ack"'),1)
        response=self.client.post('/neoscorpion/fuel-dispatch/call-dispatch/ack',data={
            'mission_id':self.mission.id,'expected_cycle':1,'alert_fingerprint':alert['fingerprint']})
        self.assertEqual(response.status_code,200)
        self.assertNotIn('data-action-key="call-dispatch"',self.html())
        self.assertIsNone(self.assignment.completed_at_utc)

    def test_truck_review_points_to_original_controls_without_cloning_them(self):
        self.prepare();self.selection.status='needs_sump';db.session.commit()
        html=self.html()
        self.assertIn('data-action-key="truck-review"',html)
        self.assertIn('data-dispatch-truck-review="'+str(self.truck.id)+'"',html)
        self.assertIn('Serviceability: NEEDS SUMP',html)
        center=html.split('data-dispatch-action-center',1)[1].split('data-dispatch-work-timeline',1)[0]
        self.assertNotIn('name="inventory_epoch"',center)
        self.assertEqual(self.selection.status,'needs_sump')
