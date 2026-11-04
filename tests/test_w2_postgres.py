"""Public identity isolation and seasonal scope reuse on disposable PostgreSQL."""
import unittest
from flask import g
from app.extensions import db
from app.models import User
from app.services.access_control import ensure_default_gateway_and_nodes,backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules
from tests import test_public_request_boundaries as public
from tests import test_w2_vacation_reads as vacation
from tests import test_motherbrain_block_out_authority_postgres as pg


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class PublicBoundaryPostgresTest(public.PublicRequestBoundaryTest):
    def setUp(self):pg.postgres_setup(self,public.fixture,super().setUp)

    def _asset_test_login(self):
        ensure_default_gateway_and_nodes();ensure_default_permission_rules()
        actor=User(username='Kessler',email='boundary-actor@example.test',role='grandmaster',is_active=True)
        set_user_password(actor,'FixturePassword123!');db.session.add(actor);db.session.flush()
        backfill_default_gateway_node_roles(actor,role='grandmaster');db.session.commit()
        self.assertEqual(self.client.post('/login',data=dict(email=actor.email,password='FixturePassword123!')).status_code,302)
        g.pop('_login_user',None)


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class VacationReadReusePostgresTest(vacation.VacationReadReuseTest):
    def setUp(self):pg.postgres_setup(self,vacation.fixture,super().setUp)
