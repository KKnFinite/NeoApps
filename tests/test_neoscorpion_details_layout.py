"""Exact current-main action/field parity across the presentation-only rebuild."""
import subprocess
import unittest
from jinja2 import ChoiceLoader, DictLoader
from app.extensions import db
from tests import test_neoscorpion_dispatch_details as cases

BASELINE='bcbf9f2d649592fda1c6355de67ce7f618ee22bb'


class DetailsLayoutRenderTest(unittest.TestCase):
    def test_controls_and_identity_fields_survive_every_layout_state(self):
        case=cases.DetailsTest();case.setUp();self.addCleanup(case.doCleanups)
        app=case.client.application;loader=app.jinja_env.loader
        templates={}
        for name in ('_dispatch_details.html','_dispatch_operations.html','_dispatch_secondary.html',
                     '_dispatch_action_center.html','_dispatch_work_timeline.html'):
            path='neonodes/neoscorpion/'+name
            templates[path]=subprocess.check_output(['git','show',BASELINE+':app/templates/'+path]).decode()
        def compare():
            current=cases.Controls(case.html())
            try:
                app.jinja_env.loader=ChoiceLoader([DictLoader(templates),loader]);app.jinja_env.cache.clear()
                original=cases.Controls(case.html())
            finally:app.jinja_env.loader=loader;app.jinja_env.cache.clear()
            self.assertFalse(current.nested)
            self.assertEqual(current.controls,original.controls)
        compare();case.prepare();compare()
        case.target('40');case.plan();compare()
        from datetime import timedelta
        case.save(case.start+timedelta(minutes=1),**case.final_data());case.off();case.submit();compare()
        from app.services import neoscorpion as fuel
        fuel.complete_fueled_assignment(case.gateway,case.dispatcher,case.assignment.id,
            physical_work_id=case.work.id,physical_sequence=case.work.physical_sequence_number)
        db.session.commit();compare()
