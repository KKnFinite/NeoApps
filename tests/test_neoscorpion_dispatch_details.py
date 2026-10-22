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


class Operations(HTMLParser):
    def __init__(self, html):
        super().__init__(); self.states={};self.feed(html)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='details' and 'data-operation-unavailable' in attrs:
            self.states[attrs['data-console-section']]=attrs['data-operation-unavailable']=='false'


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

    def assert_controls_preserved(self,before,current):
        self.assertFalse(before.controls-current.controls,'An original operational control was removed')
        # The newly repeatable planner stays available with an outstanding
        # plan. Its one creation form and queue-tail token are intentional.
        # The presentation-only mission locator adds exactly one unnamed input.
        allowed={('form','/neoscorpion/fuel-dispatch/pending-uplift'),
            ('select','proposed_truck_id',None),('input',None,None),*(('input',name,None) for name in (
                'expected_last_plan_ref','expected_work_ref','plan_kind','pending_action',
                'assignment_id','expected_cycle','expected_tail'))}
        # Package 1 adds identity/state guards to the two existing TOP OFF
        # forms. They are safety fields, not duplicated operational actions.
        guard_additions={('input','operation_id',None):2,
                         ('input','top_off_action_state',None):2}
        allowed.update(guard_additions)
        extra=current.controls-before.controls
        self.assertTrue(set(extra)<=allowed,extra)
        self.assertTrue(all(count==guard_additions.get(control,1)
                            for control,count in extra.items()),extra)

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
        self.assert_controls_preserved(old,new)
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
        self.assertIn('data-milestone="off"',html)
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id,
            physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number);db.session.commit()
        html=self.html()
        history=html.split('data-console-section="history"',1)[1]
        self.assertIn('data-console-visit="'+str(self.work.id)+'"',history)
        self.assertIn('VERIFIED',history)
        timeline=html.split('data-dispatch-work-timeline',1)[1].split('data-details-region="notes"',1)[0]
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

    def test_untouched_missing_data_is_not_an_extra_action_and_details_are_minimal(self):
        self.tail.inbound_fuel_lbs=None;self.mission.planned_fuel_load=None
        db.session.delete(self.assignment);db.session.commit()
        html=self.html();states=Operations(html).states
        self.assertEqual(states,{'second-truck':False,'start-uplift':False,'start-defuel':False})
        self.assertNotIn('data-dispatch-action-open',html)
        self.assertNotIn('FUEL & FLIGHT DATA',html)
        self.assertNotIn('No current visit notes',html)
        self.assertNotIn('No finalized physical visits',html)
        self.assertIn('aria-label="Dispatcher actions" hidden',html)
        self.assertIn('data-dispatch-action-count hidden',html)

    def test_second_truck_is_discoverable_before_and_during_fueling_and_not_duplicated(self):
        self.assertTrue(Operations(self.html()).states['second-truck'])
        self.prepare();html=self.html();states=Operations(html).states
        self.assertEqual(states,{'second-truck':True,'start-uplift':False,'start-defuel':False})
        self.plan();html=self.html()
        self.assertTrue(Operations(html).states['second-truck'])
        self.assertEqual(html.count('data-pending-uplift-form'),2)
        self.assertIn('name="expected_last_plan_ref"',html)
        self.assertIn('SAVE TRUCK PLAN',html)

    def complete_physical(self):
        self.prepare();self.save(self.start+timedelta(minutes=10),**self.final_data());self.off();self.submit()
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()

    def test_completed_physical_fueling_exposes_both_follow_ups_before_history(self):
        self.complete_physical();html=self.html()
        self.assertEqual(Operations(html).states,{'second-truck':False,'start-uplift':True,'start-defuel':True})
        self.assertLess(html.index('data-console-section="start-uplift"'),html.index('data-console-region="times"'))
        # A single current visit is already displayed once in the timeline;
        # do not add an empty/duplicate historical disclosure for it.
        self.assertNotIn('data-console-section="history"',html)
        self.assertNotIn('<details open',html)

    def test_active_uplift_and_defuel_keep_operations_and_existing_cancel_workflow(self):
        self.complete_physical()
        fuel.start_follow_up_fuel_cycle(self.gateway,self.dispatcher,self.assignment.id,'defuel','20',
            self.fueler.id,self.truck.id,expected_cycle=1,expected_tail=self.mission.assigned_tail_number);db.session.commit()
        html=self.html()
        self.assertEqual(Operations(html).states,{'second-truck':False,'start-uplift':False,'start-defuel':False})
        self.assertIn('CANCEL DEFUEL',html)

    def test_active_uplift_allows_second_truck_but_not_an_overlapping_follow_up(self):
        self.complete_physical()
        fuel.start_follow_up_fuel_cycle(self.gateway,self.dispatcher,self.assignment.id,'uplift','40',
            None,None,expected_cycle=1,expected_tail=self.mission.assigned_tail_number);db.session.commit()
        html=self.html()
        self.assertEqual(Operations(html).states,{'second-truck':True,'start-uplift':False,'start-defuel':False})
        self.assertIn('CANCEL UPLIFT',html)

    def test_fob_never_exposes_a_physical_follow_up(self):
        from datetime import datetime
        self.assignment.fuel_on_board_at_utc=datetime.utcnow();db.session.commit()
        html=self.html()
        self.assertEqual(Operations(html).states,{'second-truck':False,'start-uplift':False,'start-defuel':False})
        self.assertIn('FOB does not establish a physical fueling event.',html)

    def test_four_regions_copy_exact_text_and_one_current_timeline(self):
        self.prepare();self.save(self.start+timedelta(minutes=10),**self.final_data());self.off()
        row=self.row();html=self.html()
        console=html.split('class="neoscorpion-dispatch-detail-grid scorpion-desktop-console"',1)[1]
        console=console.split('</td>',1)[0]
        self.assertEqual(console.count('data-console-region='),4)
        self.assertEqual(console.count('data-copy-value='),1)
        from markupsafe import escape
        self.assertIn('data-copy-value="'+str(escape(row['load_planning_output']))+'"',console)
        for milestone in ('assigned','on','off','complete'):
            self.assertEqual(console.count('data-milestone="'+milestone+'"'),1)
        self.assertNotIn('OFF TIME',console)
        self.assertNotIn('name="assigned_truck_id"',console)
        self.assertNotIn('name="assigned_fueler_user_id"',console)
        # Physical OFF is never presented as administrative completion.
        self.assertIn('data-milestone="complete"><small>MISSION COMPLETE</small><time>—</time>',console)
        self.submit()
        self.assertIn('data-milestone="complete"><small>MISSION COMPLETE</small><time>—</time>',self.html())
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assertIn('data-milestone="complete"><small>MISSION COMPLETE</small><time datetime=',self.html())

    def test_current_production_control_inventory_is_preserved_in_each_state(self):
        names=('_dispatch_details.html','_dispatch_operations.html','_dispatch_secondary.html',
               '_dispatch_action_center.html','_dispatch_work_timeline.html','_dispatch_visit.html')
        templates={'neonodes/neoscorpion/'+name:subprocess.check_output(['git','show',
            'e40d7f413d184bdd584ddee9adfff8f3d6340c3b:app/templates/neonodes/neoscorpion/'+name]).decode() for name in names}
        app=self.client.application;loader=app.jinja_env.loader
        def compare():
            current=Controls(self.html())
            try:
                app.jinja_env.loader=ChoiceLoader([DictLoader(templates),loader]);app.jinja_env.cache.clear()
                before=Controls(self.html())
            finally:
                app.jinja_env.loader=loader;app.jinja_env.cache.clear()
            self.assertFalse(current.nested)
            self.assert_controls_preserved(before,current)
        compare();self.prepare();compare();self.plan();compare()
        self.save(self.start+timedelta(minutes=5),**self.final_data());self.off();compare();self.submit();compare()
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id,
            physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number);db.session.commit();compare()
