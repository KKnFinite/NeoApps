"""One disposable PostgreSQL check for the changed bounded JSON/history query."""
import unittest
from app.extensions import db
from app.services import neoscorpion as fuel
from app.services.neoscorpion_prof import load_observation_jobs
from app.services.neoscorpion_spear_calibration import learning_reasons
from tests import test_neoscorpion_submission_postgres as pg


@unittest.skipUnless(pg.pg.URI,'Requires disposable local PostgreSQL')
class RollingLearningPostgresTest(unittest.TestCase):
    setUp=pg.SubmissionPostgresTest.setUp
    drop_schema=pg.SubmissionPostgresTest.drop_schema
    prepare=pg.SubmissionPostgresTest.prepare
    save=pg.SubmissionPostgresTest.save
    off=pg.SubmissionPostgresTest.off
    final_data=pg.SubmissionPostgresTest.final_data
    row=pg.SubmissionPostgresTest.row
    baseline=pg.SubmissionPostgresTest.baseline
    submit=pg.SubmissionPostgresTest.submit

    def test_completed_history_projection_has_valid_json_and_excludes_test_origin(self):
        from datetime import timedelta
        self.save(self.start+timedelta(minutes=10),**self.final_data());self.off();self.submit()
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        jobs=load_observation_jobs(self.gateway.id,learning=True)
        self.assertEqual(len(jobs),1)
        self.assertIsInstance(jobs[0]['facts']['last_forecast'],dict)
        self.assertIn('Test/simulated evidence',learning_reasons(jobs[0]))
        self.assertEqual(load_observation_jobs(self.gateway.id+100,learning=True),[])
