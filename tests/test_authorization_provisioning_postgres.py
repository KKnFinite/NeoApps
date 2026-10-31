"""Two-session uniqueness races on a disposable loopback PostgreSQL schema."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4
from itertools import count

from flask import Flask
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url

from app.extensions import db
from app.models import Gateway, GatewayMembership, GatewayNodeRole, NeoNode, PortalAppAccess, User
from app.services import access_control as access

URI = os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@unittest.skipUnless(URI, 'Requires disposable loopback PostgreSQL')
class AuthorizationProvisioningPostgresTest(unittest.TestCase):
    def setUp(self):
        url=make_url(URI)
        if url.host not in {'127.0.0.1','localhost'} or not url.database.startswith('neostaffing_test_'):
            raise RuntimeError('Refusing non-disposable database')
        self.schema='authorization_'+uuid4().hex
        self.engine=create_engine(URI)
        with self.engine.begin() as connection:connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.app=Flask(__name__)
        self.app.config.update(SQLALCHEMY_DATABASE_URI=URI,SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SQLALCHEMY_ENGINE_OPTIONS={'connect_args':{'options':f'-csearch_path={self.schema} -clock_timeout=10000 -cstatement_timeout=15000'}})
        db.init_app(self.app)
        with self.app.app_context():
            db.metadata.create_all(db.engine,tables=[m.__table__ for m in (User,Gateway,NeoNode,GatewayMembership,GatewayNodeRole,PortalAppAccess)])
            user=User(username='race-user',password_hash='isolated-test-only',role='master')
            db.session.add(user);db.session.flush()
            membership=access.backfill_default_gateway_node_roles(user,'master');db.session.commit()
            self.user_id=user.id;self.membership_id=membership.id

    def tearDown(self):
        with self.app.app_context():db.session.remove();db.engine.dispose()
        with self.engine.begin() as connection:connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.engine.dispose()

    def race(self,model,action,disabled_wins=False):
        barrier=Barrier(2); committed=Event()
        def synchronized_insert(_mapper,_connection,target):
            barrier.wait(timeout=10)
            if disabled_wins and target.is_active:
                if not committed.wait(timeout=10):raise RuntimeError('Disabled winner did not commit')
        event.listen(model,'before_insert',synchronized_insert)
        try:
            def run(_):
                with self.app.test_request_context('/_concurrent-provision'):
                    user=db.session.get(User,self.user_id)
                    result=action(user)
                    db.session.commit()
                    committed.set()
                    # A contained uniqueness conflict must leave this session usable.
                    self.assertEqual(db.session.execute(text('SELECT 1')).scalar_one(),1)
                    db.session.remove()
                    return result
            with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(run,range(2)))
        finally:event.remove(model,'before_insert',synchronized_insert)

    def test_legacy_app_creation_conflict_preserves_transaction_and_caller_write(self):
        with self.app.app_context():
            PortalAppAccess.query.filter_by(user_id=self.user_id,app_code='neogateway').delete();db.session.commit()
        caller_ids=count()
        def action(user):
            db.session.add(Gateway(code='TEST'+str(next(caller_ids)),name='Caller write',is_active=True))
            return access.get_user_app_access(user,'neogateway').role
        self.assertEqual(self.race(PortalAppAccess,action),['master','master'])
        with self.app.app_context():
            self.assertEqual(PortalAppAccess.query.count(),1)
            self.assertEqual(Gateway.query.filter(Gateway.code.like('TEST%')).count(),2)

    def test_concurrent_membership_requests_create_one_pending_membership(self):
        with self.app.app_context():
            GatewayNodeRole.query.delete();GatewayMembership.query.delete();db.session.commit()
        def action(user):return access.request_default_gateway_access_for_user(user).status
        self.assertEqual(self.race(GatewayMembership,action),['pending','pending'])
        with self.app.app_context():
            self.assertEqual(GatewayMembership.query.count(),1)
            self.assertEqual(GatewayMembership.query.one().status,'pending')

    def test_concurrent_missing_node_seeding_creates_one_role(self):
        with self.app.app_context():
            node=NeoNode.query.filter_by(code='rain').one();self.node_id=node.id
            GatewayNodeRole.query.filter_by(node_id=node.id).delete();db.session.commit()
        self.assertEqual(self.race(GatewayNodeRole,lambda user:access.get_user_node_role(user,'RFD','rain')),['master','master'])
        with self.app.app_context():
            self.assertEqual(GatewayNodeRole.query.filter_by(node_id=self.node_id).count(),1)

    def test_race_winner_disabled_app_is_not_overwritten(self):
        with self.app.app_context():
            PortalAppAccess.query.delete();db.session.commit()
        # Both see missing; one inserts disabled, the other approved. The winner
        # must be returned unchanged rather than overwritten by the loser.
        contender=count()
        results=self.race(PortalAppAccess,lambda user:access._get_or_create_grant(
            PortalAppAccess,{'user_id':user.id,'app_code':'neostaffing'},
            {'status':'approved','role':'watcher','is_active':bool(next(contender))})[0].is_active,disabled_wins=True)
        self.assertEqual(results,[False,False])
        with self.app.app_context():
            self.assertEqual(PortalAppAccess.query.count(),1)
            self.assertFalse(PortalAppAccess.query.one().is_active)

    def test_concurrent_revoked_node_resolution_and_seeding_never_restores(self):
        with self.app.app_context():
            node=NeoNode.query.filter_by(code='rain').one();self.node_id=node.id
            role=GatewayNodeRole.query.filter_by(node_id=node.id).one();role.role='watcher';role.is_active=False;db.session.commit()
        def run(_):
            with self.app.test_request_context('/_concurrent-denial'):
                user=db.session.get(User,self.user_id)
                membership=db.session.get(GatewayMembership,self.membership_id)
                access.seed_gateway_node_roles(membership,'master');db.session.commit()
                return access.get_user_node_role(user,'RFD','rain')
        with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(list(pool.map(run,range(2))),[None,None])
        with self.app.app_context():
            role=GatewayNodeRole.query.filter_by(node_id=self.node_id).one()
            self.assertEqual((role.role,role.is_active),('watcher',False))

    def test_concurrent_default_creation_preserves_unique_gateway_and_nodes(self):
        with self.app.app_context():
            GatewayNodeRole.query.delete();GatewayMembership.query.delete()
            PortalAppAccess.query.delete();NeoNode.query.delete();Gateway.query.delete();db.session.commit()
        results=self.race(Gateway,lambda user:access.ensure_default_gateway_and_nodes().code)
        self.assertEqual(results,['RFD','RFD'])
        with self.app.app_context():
            self.assertEqual(Gateway.query.count(),1)
            self.assertEqual(NeoNode.query.count(),len(access.DEFAULT_NEONODES))
