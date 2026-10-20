"""Numeric-slot markup must not change any operational form contract."""
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
                self.assertEqual(cases.Controls(original).controls,cases.Controls(current).controls,state)
                self.assertFalse(cases.Controls(current).nested)
                self.assertEqual(current.count('class="neoscorpion-dispatch-number-stack'),5)
                self.assertIn('data-dispatch-actual>',current)
                self.assertIn('aria-label="T/F ',current)
                if state=='measured':self.assertIn('data-dispatch-apu-editor',current)
        finally:
            app.jinja_env.loader=loader;app.jinja_env.cache.clear()
