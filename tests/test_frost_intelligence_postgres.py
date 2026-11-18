"""Real isolated PostgreSQL setting races, revocation and atomic audit history."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest import TestCase, skipUnless
from uuid import uuid4

from flask_login import login_user
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from app.extensions import db
from app.models import AccountSecurityEvent, FrostHistoryTask, Gateway, User
from app.services import frost_intelligence as intelligence
from tests import test_account_foundation_postgres as fixtures
from tests.test_account_security_phase1 import fixture_user


@skipUnless(os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL'), 'Requires disposable loopback PostgreSQL')
class IntelligencePostgresTest(TestCase):
    setUpClass = classmethod(fixtures.AccountFoundationPostgresTest.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.AccountFoundationPostgresTest.tearDownClass.__func__)

    def prepare(self):
        with self.app.app_context():
            actor=fixture_user('frost-'+uuid4().hex[:10],platform='grandmaster')
            gateway=Gateway(code='F'+uuid4().hex[:6],name='Synthetic FROST policy',is_active=True)
            db.session.add(gateway);db.session.commit()
            return actor.id,gateway.id

    def run_as(self, actor_id, callback):
        with self.app.test_request_context():
            login_user(db.session.get(User,actor_id))
            try:
                result=callback();db.session.commit();return result
            except (PermissionError,ValueError,RuntimeError,DBAPIError) as error:
                db.session.rollback();return type(error).__name__

    def values(self, **changes):
        return dict({key:False for key in intelligence.KEYS},**changes)

    def test_concurrent_saves_have_one_winner_and_one_immutable_audit(self):
        actor,gateway=self.prepare();start=Barrier(2)
        def save(values):
            start.wait(5)
            return self.run_as(actor,lambda:intelligence.save(gateway,values,0)['revision'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(save,[self.values(automatic_retraining=True),self.values(learn_from_live_operations=True)]))
        self.assertCountEqual(results,[1,'ValueError'])
        with self.app.app_context():
            rows=AccountSecurityEvent.query.filter_by(actor_user_id=actor,action='frost_intelligence_changed').all()
            self.assertEqual(len(rows),1)
            before=rows[0].details
            self.assertEqual(intelligence.read(gateway)['revision'],1)
            with self.assertRaises(DBAPIError):
                db.session.execute(text('UPDATE account_security_events SET details=\'{}\' WHERE id=:id'),{'id':rows[0].id})
                db.session.commit()
            db.session.rollback()
            self.assertEqual(db.session.get(AccountSecurityEvent,rows[0].id).details,before)

    def test_rollback_leaves_no_control_or_audit(self):
        actor,gateway=self.prepare()
        def fail():
            intelligence.save(gateway,self.values(automatic_retraining=True),0)
            db.session.flush()
            raise RuntimeError('Injected transaction failure')
        self.assertEqual(self.run_as(actor,fail),'RuntimeError')
        with self.app.app_context():
            self.assertEqual(FrostHistoryTask.query.filter_by(gateway_id=gateway,kind=intelligence.KIND).count(),0)
            self.assertEqual(AccountSecurityEvent.query.filter_by(actor_user_id=actor).count(),0)

    def test_revocation_while_setting_save_waits_rechecks_platform_authority(self):
        actor,gateway=self.prepare();waiting=Event()
        def observed(_connection,_cursor,statement,*_args):
            if 'users' in statement.lower() and 'FOR UPDATE' in statement:
                waiting.set()
        with self.app.app_context():
            locked=User.query.filter_by(id=actor).with_for_update().one()
            event.listen(self.engine,'before_cursor_execute',observed)
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future=pool.submit(self.run_as,actor,lambda:intelligence.save(gateway,self.values(automatic_retraining=True),0))
                    try:
                        self.assertTrue(waiting.wait(8),'Setting writer did not reach the account lock')
                        locked.role='watcher';db.session.commit()
                    finally:
                        db.session.rollback()
                    self.assertEqual(future.result(timeout=15),'PermissionError')
            finally:
                event.remove(self.engine,'before_cursor_execute',observed)
            self.assertEqual(FrostHistoryTask.query.filter_by(gateway_id=gateway,kind=intelligence.KIND).count(),0)
            self.assertEqual(AccountSecurityEvent.query.filter_by(actor_user_id=actor).count(),0)
