"""Only disposable fixtures: canonical facts, immutable revisions and matching."""
import unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from app.extensions import db
from app.models import FrostHistoryTask as Task
from app.services import frost_departures as dep, frost_history as jobs, frost_auto as auto
from tests import test_neosubzero_ucc as fixtures


class DepartureTest(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.NeoSubZeroUccTest();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.m=self.f.alpha
        self.at=datetime.utcnow().replace(microsecond=0)-timedelta(hours=1)

    def actual(self,source='google_motherbrain'):
        self.m.tail_updated_at=self.at-timedelta(hours=2)
        self.m.actual_block_out_datetime_utc=self.at
        self.m.actual_block_out_source=source
        self.m.departure_status='departed'
        db.session.commit()
        return self.index()

    def index(self):return Task.query.filter_by(kind='departure_index',identity=str(self.m.id)).one_or_none()

    def outcome(self,at=None,tail=None,activity='DEPARTURE',review=False):
        at=at or self.at-timedelta(minutes=20)
        row=jobs.task(self.f.gateway.id,'xchange_outcome','verified-record',auto.VERSION)
        row.status='requires_review' if review else 'unmatched';row.input_hash='a'*64
        row.source_key='private/original-page'
        row.summary=dict(tail=tail or self.m.assigned_tail_number,event_at_utc=dep.stamp(at),
            classification=dict(activity=activity,label='LIKELY FROST PRESENT'),capture_ids=[],source=dict(source_row=2))
        db.session.flush()
        result=dep.correlate(self.f.gateway.id,row);db.session.commit();return result

    def test_scheduled_and_departed_without_actual_are_not_evidence(self):
        self.m.departure_status='departed';db.session.commit()
        self.assertIsNone(self.index())
        self.assertEqual(dep.status(self.f.gateway.id)['actual'],0)

    def test_actual_sources_and_no_guessed_park_or_exposure(self):
        for source in ('alp','unknown','api'):
            row=self.actual(source);self.assertEqual(row.status,'requires_review')
        row=self.actual('manual');self.assertEqual(row.status,'qualified')
        self.assertIsNone(row.summary['payload']['parking'])
        self.assertIsNone(row.summary['payload']['exposure'])
        self.assertEqual(row.summary['payload']['outcome'],'UNKNOWN')

    def test_tail_swap_after_blockout_keeps_physical_tail(self):
        old=self.actual().summary['payload']['tail']
        self.m.assigned_tail_number='N999';self.m.tail_updated_at=datetime.utcnow();db.session.commit()
        self.assertEqual(self.index().summary['payload']['tail'],old)
        self.assertEqual(self.index().status,'qualified')
        # New actual time plus different tail is ambiguous, not silent rewrite.
        self.m.actual_block_out_datetime_utc=self.at-timedelta(minutes=5);db.session.commit()
        self.assertEqual(self.index().status,'requires_review')

    def test_swap_before_blockout_and_late_unknown_tail_history(self):
        self.m.assigned_tail_number='N999';self.actual()
        self.assertEqual(self.index().summary['payload']['tail'],'N999')
        self.m.actual_block_out_datetime_utc=None;db.session.commit()
        self.m.actual_block_out_datetime_utc=self.at;self.m.tail_updated_at=datetime.utcnow();db.session.commit()
        # Same actual restores the previously frozen physical tail, not roster.
        self.assertEqual(self.index().summary['payload']['tail'],'N999')

    def test_google_removal_cancellation_corrections_supersede_links(self):
        self.actual();match=self.outcome();self.assertEqual(match.status,'matched')
        before=Task.query.filter_by(kind='departure_revision').count()
        self.m.actual_block_out_datetime_utc=None;self.m.actual_block_out_source='unknown';db.session.commit()
        self.assertEqual(self.index().status,'withdrawn')
        db.session.refresh(match);self.assertEqual(match.status,'superseded')
        self.assertGreater(Task.query.filter_by(kind='departure_revision').count(),before)
        self.actual();self.m.departure_status='cancelled';db.session.commit()
        self.assertEqual(self.index().status,'withdrawn')

    def test_atomic_rollback_and_unrelated_edits_do_not_duplicate(self):
        self.actual();count=Task.query.filter_by(kind='departure_revision').count()
        self.m.destination='ORD';db.session.commit()
        self.assertEqual(Task.query.filter_by(kind='departure_revision').count(),count)
        self.m.actual_block_out_datetime_utc=None;db.session.flush();db.session.rollback()
        self.assertEqual(self.index().status,'qualified')
        self.assertEqual(Task.query.filter_by(kind='departure_revision').count(),count)

    def test_late_outcome_unique_overnight_and_overlap_no_negative(self):
        self.actual();match=self.outcome()
        self.assertEqual(match.status,'matched');self.assertEqual(match.summary['mission_id'],self.m.id)
        self.outcome();self.assertEqual(Task.query.filter_by(kind='departure_match').count(),1)
        self.assertEqual(Task.query.filter_by(kind='departure_link').count(),1)
        state=dep.status(self.f.gateway.id)
        self.assertEqual((state['actual'],state['linked'],state['unknown']),(1,1,0))
        self.outcome(tail='N-NOT-IN-CSV');self.assertEqual(dep.status(self.f.gateway.id)['unknown'],1)

    def test_multiple_departures_require_review_and_pretreat_window(self):
        self.actual()
        second=self.f._departure('5X999',self.m.assigned_tail_number,'ORD',5)
        second.actual_block_out_datetime_utc=self.at+timedelta(hours=1)
        second.actual_block_out_source='manual';second.tail_updated_at=self.at-timedelta(hours=2)
        db.session.commit()
        self.assertEqual(self.outcome().status,'requires_review')
        self.assertEqual(self.outcome(at=self.at-timedelta(hours=12),activity='PRETREAT').status,'requires_review')
        self.assertEqual(self.outcome(at=self.at-timedelta(hours=12)).status,'unknown')

    def test_future_actual_and_untrusted_tail_excluded(self):
        self.actual();self.m.actual_block_out_datetime_utc=datetime.utcnow()+timedelta(hours=1);db.session.commit()
        self.assertEqual(self.index().status,'requires_review')

    def test_discovery_is_bounded_and_memory_defers_without_storage(self):
        with patch.object(jobs,'memory_allows_work',return_value=False),patch.object(dep.vault,'transport_scope') as storage:
            self.assertEqual(dep.drain(self.f.gateway.id),0);storage.assert_not_called()

    def test_verified_parking_exposure_and_identity_reassignment(self):
        from app.models import SortDateParkingAssignment as Parking
        self.m.tail_updated_at=self.at-timedelta(hours=4)
        parking=Parking.query.filter_by(tail_number=self.m.assigned_tail_number).one()
        parking.updated_at=self.at-timedelta(hours=3)
        arrival=self.f._departure('5X-ARR',self.m.assigned_tail_number,'RFD',1)
        arrival.mission_type='arrival';arrival.actual_block_in_datetime_utc=self.at-timedelta(hours=2)
        arrival.actual_block_in_source='manual';arrival.tail_updated_at=self.at-timedelta(hours=3)
        self.actual()
        value=self.index().summary['payload']
        self.assertEqual(value['parking']['position'],'A03')
        self.assertEqual(value['exposure']['minutes'],120)
        parking.updated_at=datetime.utcnow();parking.position_code='A04';db.session.commit()
        self.m.flight_number='5X-REASSIGNED';db.session.commit()
        self.assertEqual(self.index().status,'requires_review')
        self.assertEqual(self.index().summary['payload']['parking']['position'],'A03')

    def test_archive_outage_retry_verified_immutable_receipt(self):
        from tests.test_frost_vault import MemoryR2
        from app.services import frost_schema
        self.actual();receipt=Task.query.filter_by(kind='departure_revision').one()
        original=receipt.input_hash
        with patch.object(dep.vault,'put_json',side_effect=RuntimeError('isolated outage')):
            dep.drain(self.f.gateway.id)
        db.session.refresh(receipt)
        self.assertEqual(receipt.status,'retry');self.assertIsNone(receipt.result_key)
        receipt.retry_at=datetime.utcnow()-timedelta(seconds=1);db.session.commit()
        store=MemoryR2()
        with patch('app.services.neoscorpion_learning_vault._r2_client',return_value=store),\
                patch('app.services.neoscorpion_learning_vault._require_r2_config',return_value={'bucket':'fixture'}):
            dep.drain(self.f.gateway.id);db.session.refresh(receipt)
            self.assertEqual(receipt.status,'ready');self.assertEqual(receipt.input_hash,original)
            payload=dep.vault.read_json(receipt.result_key,self.f.gateway.id)
            self.assertEqual(dep.vault.digest(dep.vault.canonical(payload)),original)
        db.session.rollback()  # Release read locks before a separate DDL connection.
        with db.engine.begin() as connection:frost_schema.install(connection);frost_schema.install(connection)
        receipt.summary={}
        with self.assertRaises(Exception):db.session.commit()
        db.session.rollback()

    def test_late_xchange_uses_original_spray_not_departure_or_import_time(self):
        from app.services import frost_xchange_links as links
        from app.services.frost_weather import STATION
        self.actual();capture_at=self.at.replace(tzinfo=timezone.utc)-timedelta(hours=1,minutes=40)
        for kind,identity,version in [('worker','memory-budget','frost-worker-v1'),
                ('auto_capture',auto.identity(capture_at),auto.VERSION)]:
            jobs.task(self.f.gateway.id,kind,identity,version).retry_at=auto.naive(capture_at-timedelta(seconds=1))
        db.session.commit()
        source=dict(station=STATION,cutoff_at_utc=capture_at.isoformat(),observations=[],sources=[],native_forecasts=[])
        from tests.test_frost_vault import MemoryR2
        store=MemoryR2()
        with patch.object(auto,'now',return_value=capture_at),patch.object(auto,'acquire',return_value=source),\
                patch('app.services.neoscorpion_learning_vault._r2_client',return_value=store),\
                patch('app.services.neoscorpion_learning_vault._require_r2_config',return_value={'bucket':'fixture'}):
            self.assertEqual(auto.tick(self.f.gateway.id),1)
        capture=Task.query.filter_by(kind='auto_capture').one();before=capture.input_hash
        item=dict(source_record_id='late-csv',row_hash='a'*64,modified_utc=None,tail=self.m.assigned_tail_number,
            source=dict(source_row=3,document_sha256='b'*64),requires_review=False,
            classification=dict(activity='DEPARTURE',label='LIKELY FROST PRESENT'),
            fluids=[dict(occurred_at_utc=dep.stamp(self.at-timedelta(minutes=20)))])
        links.link(self.f.gateway.id,item,'private-source');db.session.commit()
        match=Task.query.filter_by(kind='departure_match').one()
        self.assertEqual(match.status,'matched');self.assertIn(capture.id,match.summary['capture_ids'])
        self.assertIsNotNone(match.summary['independent_night'])
        self.assertEqual(Task.query.filter_by(kind='auto_match',status='active').count(),1)
        self.assertEqual(dep.status(self.f.gateway.id)['unavailable_forecasts'],1)
        self.assertEqual(capture.input_hash,before)  # Departures never alter weather/model evidence.
        links.link(self.f.gateway.id,item,'overlapping-export');db.session.commit()
        self.assertEqual(Task.query.filter_by(kind='departure_match').count(),1)

    def test_deleted_actual_stages_withdrawal_without_orphan_authority(self):
        self.actual();identifier=self.m.id;db.session.delete(self.m);db.session.commit()
        self.assertEqual(Task.query.filter_by(kind='departure_index',identity=str(identifier)).one().status,'withdrawn')

    def test_stale_discovery_cannot_replace_corrected_canonical_state(self):
        from types import SimpleNamespace
        self.actual()
        stale=SimpleNamespace(id=self.m.id)
        self.m.actual_block_out_datetime_utc=self.at-timedelta(minutes=5);db.session.commit()
        before=self.index().input_hash
        dep.stage(db.session.connection(),stale,historical=True);db.session.commit()
        self.assertEqual(self.index().input_hash,before)


from tests.test_frost_phase12 import ArchiveAccessTest
class DepartureAccessTest(ArchiveAccessTest):
    def test_private_receipt_access_authorization_hash_and_memory(self):
        from flask import g
        from tests.test_frost_vault import MemoryR2
        mission=self.f.alpha;at=datetime.utcnow()-timedelta(minutes=20)
        mission.tail_updated_at=at-timedelta(hours=1);mission.actual_block_out_datetime_utc=at
        mission.actual_block_out_source='manual';db.session.commit()
        store=MemoryR2()
        with patch('app.services.neoscorpion_learning_vault._r2_client',return_value=store),\
                patch('app.services.neoscorpion_learning_vault._require_r2_config',return_value={'bucket':'fixture'}):
            dep.drain(self.f.gateway.id)
            client=self.fixture.fixture.client('master');watcher=self.fixture.fixture.client('watcher')
            def get(who,path):g.pop('_login_user',None);return who.get(path)
            path='/neosubzero/frost-vault/departure-evidence'
            self.assertEqual(get(watcher,path).status_code,403)
            catalog=get(client,path);self.assertEqual(catalog.status_code,200)
            entry=catalog.json['evidence'][0]
            self.assertEqual(get(watcher,entry['url']).status_code,403)
            payload=get(client,entry['url']);self.assertEqual(payload.status_code,200)
            self.assertEqual(dep.vault.digest(dep.vault.canonical(payload.json)),entry['sha256'])
            self.assertIn('no-store',payload.headers['Cache-Control'])
            with patch.object(jobs,'memory_allows_work',return_value=False):
                self.assertEqual(get(client,entry['url']).status_code,503)


from tests import test_neosubzero_ucc_postgres as pg
@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class PostgresDepartureTest(DepartureTest):
    drop_schema=pg.UccPostgresTest.drop_schema
    def setUp(self):
        pg.UccPostgresTest.setUp(self);existing=self.fixture
        with patch.object(fixtures,'NeoSubZeroUccTest',return_value=existing),patch.object(existing,'setUp'),patch.object(existing,'tearDown'):
            DepartureTest.setUp(self)
