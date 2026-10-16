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
        self.assertEqual(dispatch_preferences(self.gateway,self.user),dict(arrived_first=True,secondary_sort='etd',truck_cards_collapsed=False,board_truck_cards_collapsed=False,statistics_collapsed=False,spear_data_collapsed=False,version=0))
        response=self.post(user_id=999,gateway_id=999)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['preferences'],dict(arrived_first=False,secondary_sort='arrival',truck_cards_collapsed=False,board_truck_cards_collapsed=False,statistics_collapsed=False,spear_data_collapsed=False,version=1))
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

    def test_sections_are_independent_sparse_patches_preserve_sort_and_old_clients(self):
        endpoint='/neoscorpion/fuel-dispatch/preferences'
        response=self.client.post(endpoint,data=dict(truck_cards_collapsed='1',expected_version='0'))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['preferences']['secondary_sort'],'etd')
        self.assertTrue(response.json['preferences']['truck_cards_collapsed'])
        self.assertFalse(response.json['preferences']['spear_data_collapsed'])
        response=self.client.post(endpoint,data=dict(spear_data_collapsed='1',expected_version='1'))
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json['preferences']['truck_cards_collapsed'])
        # Old sorting-only clients cannot reopen sections.
        self.assertEqual(self.client.post(endpoint,data=dict(arrived_first='0',secondary_sort='arrival',expected_version='2')).status_code,200)
        self.assertEqual(self.client.post(endpoint,data=dict(truck_cards_collapsed='0',expected_version='2')).status_code,409)
        prefs=dispatch_preferences(self.gateway,self.user)
        self.assertTrue(prefs['truck_cards_collapsed']);self.assertTrue(prefs['spear_data_collapsed'])
        self.assertEqual(prefs['secondary_sort'],'arrival')
        self.assertEqual(NeoScorpionSortAssetState.query.count(),0)
        for value in ('true','yes','-1',''):
            self.assertEqual(self.client.post(endpoint,data=dict(truck_cards_collapsed=value,expected_version='3')).status_code,400)
        self.assertEqual(self.client.post(endpoint,data=dict(expected_version='3')).status_code,400)

    def test_additive_columns_default_expanded_preserve_existing_sort_profile(self):
        from sqlalchemy import text
        from app.services.schema_sync import sync_local_sqlite_schema
        self.assertEqual(self.post().status_code,200)
        before=(self.user.id,self.gateway.id)
        db.session.execute(text('ALTER TABLE neoscorpion_dispatch_preferences DROP COLUMN truck_cards_collapsed'))
        db.session.execute(text('ALTER TABLE neoscorpion_dispatch_preferences DROP COLUMN spear_data_collapsed'))
        db.session.commit()
        for _ in range(2):sync_local_sqlite_schema(self.fixture.app);db.session.commit()
        db.session.expire_all()
        record=Preference.query.one()
        self.assertEqual((record.user_id,record.gateway_id),before)
        self.assertEqual((record.secondary_sort,record.arrived_first,record.version),('arrival',False,1))
        self.assertFalse(record.truck_cards_collapsed);self.assertFalse(record.spear_data_collapsed)

    def test_board_and_statistics_persist_without_overwriting_normal_view(self):
        for version,patch in enumerate((dict(truck_cards_collapsed='1'),dict(board_truck_cards_collapsed='0'),dict(statistics_collapsed='1'))):
            result=self.client.post('/neoscorpion/fuel-dispatch/preferences',data=dict(expected_version=version,**patch))
            self.assertEqual(result.status_code,200)
        uid=self.user.id;gid=self.gateway.id;db.session.remove()
        from app.models import User
        prefs=dispatch_preferences(db.session.get(Gateway,gid),db.session.get(User,uid))
        self.assertTrue(prefs['truck_cards_collapsed']);self.assertFalse(prefs['board_truck_cards_collapsed'])
        self.assertTrue(prefs['statistics_collapsed']);self.assertEqual(prefs['version'],3)


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

    def test_additive_section_columns_preserve_existing_profile_and_work(self):
        from sqlalchemy import text
        from app.services.neoscorpion_schema import ensure_neoscorpion_production_schema
        save_dispatch_preferences(self.gateway,self.dispatcher,dict(secondary_sort='arrival',arrived_first='0',expected_version='0'))
        db.session.commit()
        for name in ('truck_cards_collapsed','board_truck_cards_collapsed','statistics_collapsed','spear_data_collapsed'):
            db.session.execute(text('ALTER TABLE neoscorpion_dispatch_preferences DROP COLUMN '+name))
        db.session.commit()
        for _ in range(2):
            with patch.dict(self.fixture.app.config,{'TESTING':False}):ensure_neoscorpion_production_schema(self.fixture.app)
        db.session.expire_all()
        prefs=dispatch_preferences(self.gateway,self.dispatcher)
        self.assertEqual(prefs,dict(arrived_first=False,secondary_sort='arrival',version=1,truck_cards_collapsed=False,board_truck_cards_collapsed=False,statistics_collapsed=False,spear_data_collapsed=False))
        self.assertIsNotNone(self.work.id)
