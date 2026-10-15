"""Personal profile isolation, validation, persistence and additive bootstrap."""
import unittest
from unittest.mock import patch
from app.extensions import db
from app.models import NeoScorpionDispatchPreference as Preference, NeoScorpionSortAssetState, Gateway
from app.services.neoscorpion_dispatch_preferences import dispatch_preferences,save_dispatch_preferences
from tests import test_neoscorpion_dispatch_planning as fixtures


class DispatchPreferencesTest(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.NeoScorpionDispatchPlanningTest();self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.gateway=self.fixture.gateway;self.client=self.fixture.client
        self.user=self.fixture._login_user('pref-dispatcher','simulator')

    def post(self,**changes):
        return self.client.post('/neoscorpion/fuel-dispatch/preferences',data=dict(
            arrived_first='0',secondary_sort='arrival',expected_version='0',**changes))

    def test_defaults_persistence_other_sessions_users_gateways_and_no_operational_change(self):
        self.assertEqual(dispatch_preferences(self.gateway,self.user),dict(arrived_first=True,secondary_sort='etd',version=0))
        response=self.post(user_id=999,gateway_id=999)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['preferences'],dict(arrived_first=False,secondary_sort='arrival',version=1))
        self.assertEqual(NeoScorpionSortAssetState.query.count(),0)
        user_id=self.user.id;gateway_id=self.gateway.id
        db.session.remove()
        from app.models import User
        user=db.session.get(User,user_id);gateway=db.session.get(Gateway,gateway_id)
        self.assertEqual(dispatch_preferences(gateway,user)['secondary_sort'],'arrival')
        other=self.fixture._login_user('different-dispatcher','simulator')
        self.assertEqual(dispatch_preferences(gateway,other)['version'],0)
        second=Gateway(code='TEST',name='Test gateway');db.session.add(second);db.session.commit()
        self.assertEqual(dispatch_preferences(second,user)['version'],0)
        self.assertEqual(Preference.query.one().user_id,user_id)
        html=self.client.get('/neoscorpion/fuel-dispatch').get_data(as_text=True)
        self.assertIn('data-sort-mode="etd"',html)

    def test_validation_conflict_and_permissions(self):
        for changes in ({'arrived_first':'yes'},{'secondary_sort':'flight'},{'expected_version':'no'}):
            data=dict(arrived_first='0',secondary_sort='arrival',expected_version='0');data.update(changes)
            self.assertEqual(self.client.post('/neoscorpion/fuel-dispatch/preferences',data=data).status_code,400)
        self.assertEqual(Preference.query.count(),0)
        self.assertEqual(self.post().status_code,200)
        self.assertEqual(self.post().status_code,409)
        self.assertEqual(Preference.query.one().version,1)
        with patch('app.neonodes.neoscorpion.routes.permission_access',return_value={'can_view':False}):
            self.assertEqual(self.post().status_code,403)
        self.assertEqual(Preference.query.one().version,1)

    def test_bootstrap_new_table_is_idempotent_and_preserves_operational_data(self):
        Preference.__table__.drop(db.engine)
        for _ in range(2):
            db.create_all()  # Existing SQLite bootstrap convention.
        self.assertEqual(Preference.query.count(),0)
        self.assertEqual(self.post().status_code,200)
        self.assertIsNotNone(self.fixture.operation.id)


from tests import test_neoscorpion_segments_postgres as postgres


@unittest.skipUnless(postgres.URI,'Requires disposable local PostgreSQL')
class DispatchPreferencesPostgresTest(unittest.TestCase):
    setUp=postgres.PhysicalSegmentPostgresTest.setUp
    drop_schema=postgres.PhysicalSegmentPostgresTest.drop_schema
    prepare=postgres.PhysicalSegmentPostgresTest.prepare
    concurrent=postgres.PhysicalSegmentPostgresTest.concurrent

    def test_repeat_bootstrap_and_concurrent_initial_profile_updates(self):
        from app.services.neoscorpion_schema import ensure_neoscorpion_production_schema
        from app.services.neoscorpion_dispatch_preferences import DispatchPreferenceConflict
        Preference.__table__.drop(db.engine)
        for _ in range(2):
            with patch.dict(self.fixture.app.config,{'TESTING':False}):
                ensure_neoscorpion_production_schema(self.fixture.app)
        def change(index,gateway,fueler,dispatcher):
            try:
                save_dispatch_preferences(gateway,dispatcher,dict(arrived_first='0',secondary_sort='arrival',expected_version='0'))
                return 'saved'
            except DispatchPreferenceConflict:
                db.session.rollback()
                return 'conflict'
        self.assertEqual(sorted(self.concurrent(change)),['conflict','saved'])
        self.assertEqual(Preference.query.count(),1)
        self.assertEqual(Preference.query.one().version,1)
        from app.models import NeoScorpionFuelWorkState
        self.assertEqual(db.session.get(NeoScorpionFuelWorkState,self.ids[-1]).id,self.ids[-1])
