"""Numeric-slot markup must not change any operational form contract."""
from flask import Response
from tests.html_contracts import document
import subprocess
import unittest
from datetime import timedelta
from jinja2 import ChoiceLoader, DictLoader
from app.extensions import db
from app.services import neoscorpion as fuel
from tests import test_neoscorpion_dispatch_details as cases


class NumberMarkupTest(unittest.TestCase):
    def test_original_controls_and_visit_identity_survive_numeric_wrappers(self):
        case=cases.DetailsTest();case.setUp();self.addCleanup(case.doCleanups)
        app=case.client.application;loader=app.jinja_env.loader
        name='neonodes/neoscorpion/_fuel_dispatch_panel.html'
        old=DictLoader({name:subprocess.check_output(['git','show',
            'df424b2428e03b4d1fd0522649d118cff900782d:app/templates/'+name]).decode()})
        try:
            for state in ('empty','measured','completed'):
                if state=='measured':
                    case.prepare();case.save(case.start+timedelta(minutes=1),**case.final_data())
                    case.work.apu_running=True;case.work.apu_allowance_lbs=500;db.session.commit()
                if state=='completed':
                    case.work.apu_running=False;case.work.apu_allowance_lbs=0;db.session.commit()
                    case.off();case.submit()
                    fuel.complete_fueled_assignment(case.gateway,case.dispatcher,case.assignment.id);db.session.commit()
                app.jinja_env.loader=loader;app.jinja_env.cache.clear();current=case.html()
                app.jinja_env.loader=ChoiceLoader([old,loader]);app.jinja_env.cache.clear();original=case.html()
                before, after = cases.Controls(original), cases.Controls(current)
                # The historical fixture may render one or two TOP OFF controls;
                # require every actual form's current guards without changing actions.
                original_forms = {k:v for k,v in before.controls.items() if k[0] in {'form','button'}}
                self.assertEqual(original_forms, {k:v for k,v in after.controls.items() if k[0] in {'form','button'}})
                self.assertFalse(before.controls - after.controls)
                extras = after.controls - before.controls
                self.assertTrue(set(extras) <= {('input','operation_id',None), ('input','top_off_action_state',None), ('input',None,None)}, extras)
                guarded=[form for form in document(Response(current)).findall('form') if form.findall('input',name='top_off_action_state')]
                self.assertEqual(len(guarded),len(document(Response(current)).findall('input',name='top_off_action_state')))
                # The report filter also owns an operation_id; it is not a TOP OFF guard.
                self.assertEqual(extras[('input',None,None)],1)  # One presentation-only mission locator.
                for control in (('input','operation_id',None),('input','top_off_action_state',None)):
                    self.assertLessEqual(extras[control],2)
                for form in guarded:
                    self.assertEqual(len(form.findall('input',name='operation_id')),1)
                    self.assertEqual(len(form.findall('input',name='top_off_action_state')),1)
                    self.assertTrue(form.one('input',name='operation_id').attrs['value'])
                    self.assertTrue(form.one('input',name='top_off_action_state').attrs['value'])
                self.assertFalse(cases.Controls(current).nested)
                self.assertEqual(current.count('class="neoscorpion-dispatch-number-stack'),5)
                self.assertIn('data-dispatch-actual>',current)
                self.assertIn('aria-label="T/F ',current)
                if state=='measured':self.assertIn('data-dispatch-apu-editor',current)
        finally:
            app.jinja_env.loader=loader;app.jinja_env.cache.clear()
